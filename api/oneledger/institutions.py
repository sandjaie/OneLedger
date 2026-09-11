"""Resolve PDF issuer branding to canonical institution UUIDs.

Institution names remain data. This module knows only how to normalize exact text, load rows the
household is allowed to use, and abstain when that evidence names zero or several institutions.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class InstitutionCandidate:
    id: UUID
    name: str
    short_name: str | None
    country: str
    institution_type: str


@dataclass(frozen=True)
class InstitutionResolution:
    status: Literal["matched", "needs_confirmation", "ambiguous"]
    proposed_name: str | None
    country: str | None
    candidates: tuple[InstitutionCandidate, ...]


class InstitutionCursor(Protocol):
    def execute(self, statement: str, values: tuple[Any, ...]) -> object: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


def _display_name(value: str) -> str | None:
    displayed = " ".join(unicodedata.normalize("NFKC", value).split())
    if not displayed or len(displayed) > 200 or len(displayed.encode()) > 800:
        return None
    return displayed


def normalize_institution_name(value: str) -> str:
    """The one institution-text transform: NFKC, collapsed whitespace and Unicode casefold."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def resolve_institution(
    cur: InstitutionCursor,
    household_id: UUID,
    evidence: str,
    *,
    country: str | None = None,
) -> InstitutionResolution:
    """Return all exact visible matches, or an explicit create/choose state.

    Canonical names and short names use the institution's country. An alias uses its own country,
    which lets one public spelling be scoped without changing the canonical institution row.
    Multiple spellings for one UUID are collapsed before the status is decided.
    """
    cur.execute(
        """SELECT i.id, i.name, i.short_name, i.country, i.institution_type,
                  ia.alias, ia.country, ia.household_id, ia.provenance
             FROM institution i
             LEFT JOIN institution_alias ia
               ON ia.institution_id = i.id AND ia.is_active
              AND (ia.household_id IS NULL OR ia.household_id = %s)
            WHERE i.household_id IS NULL OR i.household_id = %s
            ORDER BY i.name, i.country, i.id, ia.created_at, ia.id""",
        (household_id, household_id),
    )
    expected = normalize_institution_name(evidence)
    expected_country = country.strip().upper() if country and country.strip() else None
    matched: dict[UUID, InstitutionCandidate] = {}
    owner_confirmed: dict[UUID, InstitutionCandidate] = {}
    for row in cur.fetchall():
        (
            identifier,
            name,
            short_name,
            institution_country,
            institution_type,
            alias,
            alias_country,
            alias_household_id,
            alias_provenance,
        ) = row
        candidate = InstitutionCandidate(
            id=identifier,
            name=name,
            short_name=short_name,
            country=institution_country,
            institution_type=institution_type,
        )
        names = [(name, institution_country)]
        if short_name:
            names.append((short_name, institution_country))
        if alias:
            names.append((alias, alias_country))
        alias_matches = bool(
            alias
            and normalize_institution_name(alias) == expected
            and (expected_country is None or alias_country == expected_country)
        )
        if (
            alias_matches
            and alias_household_id == household_id
            and alias_provenance == "owner_confirmed"
        ):
            owner_confirmed[identifier] = candidate
        if any(
            normalize_institution_name(value) == expected
            and (expected_country is None or name_country == expected_country)
            for value, name_country in names
        ):
            matched[identifier] = candidate

    candidates = tuple(
        sorted(
            (owner_confirmed or matched).values(),
            key=lambda item: (
                normalize_institution_name(item.name),
                item.country,
                str(item.id),
            ),
        )
    )
    status: Literal["matched", "needs_confirmation", "ambiguous"]
    if len(candidates) == 1:
        status = "matched"
    elif candidates:
        status = "ambiguous"
    else:
        status = "needs_confirmation"
    return InstitutionResolution(
        status=status,
        proposed_name=_display_name(evidence),
        country=expected_country,
        candidates=candidates,
    )
