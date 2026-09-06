# api/oneledger/routes/categories.py
"""M1b: the category taxonomy for pickers and Settings. The seeded tree is shared; a
household adds its own rows and may re-bucket any category — the bucket is an editable
default, not a verdict (ADR-0008). Renaming is for the household's own rows; the seed set
keeps its names so seed updates stay recognisable."""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(tags=["categories"])

BUCKETS = frozenset({"need", "want", "investment", "debt_service", "income", "ignore"})


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0]


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not cleaned:
        raise HTTPException(status_code=422, detail="a category name needs word characters")
    return cleaned


class CategoryRow(BaseModel):
    id: UUID
    parentId: UUID | None
    name: str
    slug: str
    kind: str
    bucket: str
    sortOrder: int
    isSystem: bool
    isActive: bool


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parentId: UUID | None = None
    # Required for a root; a child inherits its parent's kind and bucket as the default.
    kind: str | None = Field(default=None, max_length=20)
    bucket: str | None = Field(default=None, max_length=20)


class CategoryUpdate(BaseModel):
    """Always sent in full. Name changes apply to the household's own rows; bucket and
    activity apply to any row, because both are stated to be the owner's defaults."""

    name: str = Field(min_length=1, max_length=80)
    bucket: str = Field(min_length=1, max_length=20)
    isActive: bool


COLUMNS = "id, parent_id, name, slug, kind::text, bucket::text, sort_order, is_system, is_active"
# The read merges the household's overrides over the seed: the seed row is versioned shared
# data the service must not rewrite, and the household's bucket/activity choices win on read.
MERGED = """c.id, c.parent_id, c.name, c.slug, c.kind::text,
            COALESCE(o.bucket, c.bucket)::text, c.sort_order, c.is_system,
            COALESCE(o.is_active, c.is_active)"""
MERGED_FROM = """FROM category c
                 LEFT JOIN category_override o
                        ON o.category_id = c.id AND o.household_id = %s"""
FIELDS = [
    "id",
    "parentId",
    "name",
    "slug",
    "kind",
    "bucket",
    "sortOrder",
    "isSystem",
    "isActive",
]


def category_row(values: tuple[Any, ...]) -> CategoryRow:
    return CategoryRow(**dict(zip(FIELDS, values, strict=True)))


@router.get("/categories")
def list_categories(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> list[CategoryRow]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT {MERGED} {MERGED_FROM}
                 WHERE c.household_id IS NULL OR c.household_id = %s
                 ORDER BY c.parent_id NULLS FIRST, c.sort_order, c.name""",
            (owner.household_id, owner.household_id),
        )
        return [category_row(row) for row in cur.fetchall()]


@router.post("/categories", status_code=201)
def create_category(
    body: CategoryCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> CategoryRow:
    if body.bucket is not None and body.bucket not in BUCKETS:
        raise HTTPException(status_code=422, detail="unknown bucket")
    try:
        with connect() as conn, conn.cursor() as cur:
            if body.parentId is None:
                if body.kind is None or body.bucket is None:
                    raise HTTPException(
                        status_code=422, detail="a root category needs a kind and a bucket"
                    )
                cur.execute(
                    f"""INSERT INTO category (household_id, name, slug, kind, bucket)
                        VALUES (%s, %s, %s, %s::category_kind, %s::category_bucket)
                        RETURNING {COLUMNS}""",
                    (owner.household_id, body.name, _slug(body.name), body.kind, body.bucket),
                )
            else:
                # A child inherits its parent's kind, and its bucket unless one is given.
                # The parent must be visible to this household; the two-level trigger
                # rejects a child of a child.
                cur.execute(
                    f"""INSERT INTO category (household_id, parent_id, name, slug, kind, bucket)
                        SELECT %s, p.id, %s, p.slug || '-' || %s, p.kind,
                               COALESCE(%s::category_bucket, p.bucket)
                          FROM category p
                         WHERE p.id = %s
                           AND (p.household_id IS NULL OR p.household_id = %s)
                        RETURNING {COLUMNS}""",
                    (
                        owner.household_id,
                        body.name,
                        _slug(body.name),
                        body.bucket,
                        body.parentId,
                        owner.household_id,
                    ),
                )
            created = cur.fetchone()
            if created is None:
                raise HTTPException(status_code=422, detail="no such parent category")
            return category_row(created)
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.UniqueViolation,
        psycopg.errors.RaiseException,
    ) as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc


@router.patch("/categories/{category_id}")
def update_category(
    category_id: UUID,
    body: CategoryUpdate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> CategoryRow:
    if body.bucket not in BUCKETS:
        raise HTTPException(status_code=422, detail="unknown bucket")
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT household_id IS NULL, name FROM category
                    WHERE id = %s AND (household_id IS NULL OR household_id = %s)""",
                (category_id, owner.household_id),
            )
            existing = cur.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="no such category")
            system_row, current_name = bool(existing[0]), str(existing[1])
            if system_row and body.name != current_name:
                raise HTTPException(
                    status_code=422, detail="a seeded category keeps its name; add your own"
                )
            if system_row:
                # Never rewrite the seed in place: the household's choice is an override row
                # beside it, upserted so repeat edits replace themselves.
                cur.execute(
                    """INSERT INTO category_override (household_id, category_id, bucket, is_active)
                       VALUES (%s, %s, %s::category_bucket, %s)
                       ON CONFLICT (household_id, category_id)
                         DO UPDATE SET bucket = EXCLUDED.bucket,
                                       is_active = EXCLUDED.is_active,
                                       updated_at = now()""",
                    (owner.household_id, category_id, body.bucket, body.isActive),
                )
            else:
                cur.execute(
                    """UPDATE category
                          SET bucket = %s::category_bucket, is_active = %s, name = %s,
                              updated_at = now()
                        WHERE id = %s AND household_id = %s""",
                    (body.bucket, body.isActive, body.name, category_id, owner.household_id),
                )
            cur.execute(
                f"SELECT {MERGED} {MERGED_FROM} WHERE c.id = %s",
                (owner.household_id, category_id),
            )
            updated = cur.fetchone()
            if updated is None:
                raise HTTPException(status_code=404, detail="no such category")
            return category_row(updated)
    except psycopg.errors.InvalidTextRepresentation as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc
