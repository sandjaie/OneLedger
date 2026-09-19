"""Bounded owner-scoped workspace search over safe display fields."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect
from .layouts import _preview_redactor

router = APIRouter(tags=["search"])

SearchKind = Literal[
    "account", "transaction", "document", "holding", "goal", "commitment", "register"
]


class SearchResult(BaseModel):
    kind: SearchKind
    resourceId: UUID
    primaryLabel: str
    secondaryLabel: str | None
    route: str
    date: date | None
    status: str | None


class SearchResponse(BaseModel):
    results: list[SearchResult]


def _rank(*fields: str) -> str:
    """Exact prefix, then a punctuation-insensitive word prefix, then contains."""
    ranks = []
    for field in fields:
        safe = f"coalesce({field}, '')"
        ranks.append(
            f"""CASE
                  WHEN lower({safe}) LIKE lower(%(query)s) || '%%' THEN 0
                  WHEN strpos(
                         ' ' || regexp_replace(lower({safe}), '[^[:alnum:]]+', ' ', 'g'),
                         ' ' || lower(%(query)s)
                       ) > 0 THEN 1
                  WHEN lower({safe}) LIKE '%%' || lower(%(query)s) || '%%' THEN 2
                  ELSE 3
                END"""
        )
    return f"LEAST({', '.join(ranks)})"


def _matches(*fields: str) -> str:
    return " OR ".join(
        f"lower(coalesce({field}, '')) LIKE '%%' || lower(%(query)s) || '%%'" for field in fields
    )


def _fetch(
    cur: psycopg.Cursor[Any], sql: str, owner: OwnerSession, query: str, limit: int
) -> list[tuple[Any, ...]]:
    cur.execute(sql, {"household": owner.household_id, "query": query, "limit": limit})
    return cur.fetchall()


@router.get("/search", response_model=SearchResponse)
def search(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    q: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> SearchResponse:
    query = q.strip()
    if not 2 <= len(query) <= 120:
        raise HTTPException(
            status_code=422, detail="q must contain 2 to 120 non-whitespace characters"
        )

    results: list[SearchResult] = []
    redact = _preview_redactor(None)
    params = (owner, query, limit)
    with connect() as conn, conn.cursor() as cur:
        # This is deliberately the first statement. Search is a read contract even if a future
        # query helper accidentally grows a side effect.
        cur.execute("SET TRANSACTION READ ONLY")

        account_fields = ("a.name", "institution.name", "identifier.masked_value")
        for row in _fetch(
            cur,
            f"""SELECT a.id, a.name,
                       nullif(concat_ws(' · ', institution.name, identifier.masked_value), ''),
                       CASE WHEN a.is_active THEN 'active' ELSE 'closed' END
                  FROM account a
                  LEFT JOIN institution
                    ON institution.id = a.institution_id
                   AND (
                     institution.household_id IS NULL
                     OR institution.household_id = a.household_id
                   )
                  LEFT JOIN LATERAL (
                    SELECT ai.masked_value
                      FROM account_identifier ai
                     WHERE ai.household_id = a.household_id
                       AND ai.account_id = a.id
                       AND ai.is_active
                     ORDER BY ai.created_at, ai.id LIMIT 1
                  ) identifier ON true
                 WHERE a.household_id = %(household)s
                   AND ({_matches(*account_fields)} OR EXISTS (
                     SELECT 1 FROM account_identifier searched
                      WHERE searched.household_id = a.household_id
                        AND searched.account_id = a.id AND searched.is_active
                        AND lower(searched.masked_value) LIKE '%%' || lower(%(query)s) || '%%'
                   ))
                 ORDER BY {_rank(*account_fields)}, lower(a.name), a.id
                 LIMIT %(limit)s""",
            *params,
        ):
            results.append(
                SearchResult(
                    kind="account",
                    resourceId=row[0],
                    primaryLabel=row[1],
                    secondaryLabel=row[2],
                    route="/accounts",
                    date=None,
                    status=row[3],
                )
            )

        transaction_fields = (
            "t.description_clean",
            "t.description_raw",
            "merchant.display_name",
            "merchant.canonical_name",
        )
        for row in _fetch(
            cur,
            f"""SELECT t.id, coalesce(t.description_clean, t.description_raw),
                       nullif(concat_ws(' · ', a.name,
                         coalesce(merchant.display_name, merchant.canonical_name)), ''),
                       t.booked_date, t.txn_type::text
                  FROM transaction t
                  JOIN account a ON a.household_id = t.household_id AND a.id = t.account_id
                  LEFT JOIN merchant
                    ON merchant.id = t.merchant_id
                   AND (merchant.household_id IS NULL OR merchant.household_id = t.household_id)
                 WHERE t.household_id = %(household)s AND ({_matches(*transaction_fields)})
                 ORDER BY {_rank(*transaction_fields)}, t.booked_date DESC, t.id
                 LIMIT %(limit)s""",
            *params,
        ):
            results.append(
                SearchResult(
                    kind="transaction",
                    resourceId=row[0],
                    primaryLabel=redact(row[1]),
                    secondaryLabel=None if row[2] is None else redact(row[2]),
                    route=f"/transactions?transactionId={row[0]}",
                    date=row[3],
                    status=row[4],
                )
            )

        document_fields = ("d.document_type::text", "a.name")
        for row in _fetch(
            cur,
            f"""SELECT d.id, replace(d.document_type::text, '_', ' '), a.name,
                       d.period_end, d.parsed_status::text
                  FROM document d
                  LEFT JOIN account a
                    ON a.household_id = d.household_id AND a.id = d.account_id
                 WHERE d.household_id = %(household)s AND ({_matches(*document_fields)})
                 ORDER BY {_rank(*document_fields)}, d.period_end DESC NULLS LAST,
                          d.uploaded_at DESC, d.id
                 LIMIT %(limit)s""",
            *params,
        ):
            results.append(
                SearchResult(
                    kind="document",
                    resourceId=row[0],
                    primaryLabel=row[1],
                    secondaryLabel=row[2],
                    route="/documents",
                    date=row[3],
                    status=row[4],
                )
            )

        holding_fields = ("security.name", "a.name")
        for row in _fetch(
            cur,
            f"""SELECT h.id, security.name, a.name, h.opened_on,
                       CASE WHEN h.closed_on IS NULL THEN 'open' ELSE 'closed' END
                  FROM holding h
                  JOIN account a ON a.household_id = h.household_id AND a.id = h.account_id
                  JOIN security
                    ON security.id = h.security_id
                   AND (security.household_id IS NULL OR security.household_id = h.household_id)
                 WHERE h.household_id = %(household)s AND ({_matches(*holding_fields)})
                 ORDER BY {_rank(*holding_fields)}, h.closed_on NULLS FIRST,
                          lower(security.name), h.id
                 LIMIT %(limit)s""",
            *params,
        ):
            results.append(
                SearchResult(
                    kind="holding",
                    resourceId=row[0],
                    primaryLabel=row[1],
                    secondaryLabel=row[2],
                    route=f"/investments/{row[0]}",
                    date=row[3],
                    status=row[4],
                )
            )

        for row in _fetch(
            cur,
            f"""SELECT id, name, target_date, state::text
                  FROM goal
                 WHERE household_id = %(household)s AND ({_matches("name")})
                 ORDER BY {_rank("name")}, target_date, lower(name), id
                 LIMIT %(limit)s""",
            *params,
        ):
            results.append(
                SearchResult(
                    kind="goal",
                    resourceId=row[0],
                    primaryLabel=row[1],
                    secondaryLabel=None,
                    route="/plans#goals",
                    date=row[2],
                    status=row[3],
                )
            )

        commitment_fields = ("c.label", "a.name")
        for row in _fetch(
            cur,
            f"""SELECT c.id, c.label, a.name, c.next_due_date,
                       CASE WHEN c.archived_at IS NULL THEN 'active' ELSE 'archived' END
                  FROM commitment c
                  JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id
                 WHERE c.household_id = %(household)s AND ({_matches(*commitment_fields)})
                 ORDER BY {_rank(*commitment_fields)}, c.next_due_date NULLS LAST,
                          lower(c.label), c.id
                 LIMIT %(limit)s""",
            *params,
        ):
            results.append(
                SearchResult(
                    kind="commitment",
                    resourceId=row[0],
                    primaryLabel=row[1],
                    secondaryLabel=row[2],
                    route="/plans#commitments",
                    date=row[3],
                    status=row[4],
                )
            )

        for row in _fetch(
            cur,
            f"""SELECT id, title, replace(kind::text, '_', ' '), key_date,
                       CASE WHEN archived_at IS NOT NULL THEN 'archived'
                            WHEN completed_at IS NOT NULL THEN 'complete' ELSE 'active' END
                  FROM register_record
                 WHERE household_id = %(household)s AND ({_matches("title")})
                 ORDER BY {_rank("title")}, key_date NULLS LAST, lower(title), id
                 LIMIT %(limit)s""",
            *params,
        ):
            results.append(
                SearchResult(
                    kind="register",
                    resourceId=row[0],
                    primaryLabel=row[1],
                    secondaryLabel=row[2],
                    route="/plans#register",
                    date=row[3],
                    status=row[4],
                )
            )

    return SearchResponse(results=results)
