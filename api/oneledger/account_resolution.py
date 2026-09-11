"""Resolve statement-derived account identity without writing or exposing source values."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from .identifiers import CompleteIdentifier, Identifier, identifier_from_source
from .institutions import InstitutionResolution

type ResolutionStatus = Literal["matched", "needs_confirmation", "ambiguous"]
type InstitutionAction = Literal["reuse", "create", "choose"]
type AccountAction = Literal["reuse", "enrich", "create", "choose"]


@dataclass(frozen=True)
class AccountCandidate:
    id: UUID
    name: str
    institution_id: UUID | None
    account_type: str
    currency: str
    account_number_masked: str
    can_reveal: bool


@dataclass(frozen=True)
class AccountResolution:
    status: ResolutionStatus
    institution_action: InstitutionAction
    account_action: AccountAction
    account_number_masked: str
    candidates: tuple[AccountCandidate, ...]
    identifier: Identifier = field(repr=False, compare=False)
    conflicts: tuple[Literal["institution", "account_type", "currency"], ...] = ()

    @property
    def account_id(self) -> UUID | None:
        if self.status == "matched" and len(self.candidates) == 1:
            return self.candidates[0].id
        return None


class AccountCursor(Protocol):
    def execute(self, statement: str, values: tuple[Any, ...]) -> object: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


def _candidates(rows: list[tuple[Any, ...]]) -> tuple[AccountCandidate, ...]:
    return tuple(
        AccountCandidate(
            id=row[0],
            name=row[1],
            institution_id=row[2],
            account_type=row[3],
            currency=str(row[4]).strip(),
            account_number_masked=row[5],
            can_reveal=bool(row[6]),
        )
        for row in rows
    )


def _exact(
    cur: AccountCursor,
    household_id: UUID,
    identifier: CompleteIdentifier,
    legacy_salt: str | None,
) -> tuple[AccountCandidate, ...]:
    columns = """a.id, a.name, a.institution_id, a.account_type::text, a.currency,
                 ai.masked_value, ai.full_value IS NOT NULL"""
    cur.execute(
        f"""SELECT DISTINCT {columns}
              FROM account a
              JOIN account_identifier ai
                ON ai.household_id = a.household_id AND ai.account_id = a.id
             WHERE a.household_id = %s AND a.is_active AND ai.is_active
               AND ai.kind = %s AND ai.normalized_value = %s
             ORDER BY a.name, a.id""",
        (household_id, identifier.kind, identifier.normalized_value),
    )
    found = _candidates(cur.fetchall())
    if found or not legacy_salt or not identifier.normalized_value.isdigit():
        return found
    digest = hashlib.sha256(f"{legacy_salt}{identifier.normalized_value}".encode()).hexdigest()
    cur.execute(
        f"""SELECT DISTINCT {columns}
              FROM account a
              JOIN account_identifier ai
                ON ai.household_id = a.household_id AND ai.account_id = a.id
             WHERE a.household_id = %s AND a.is_active AND ai.is_active
               AND ai.kind = %s AND ai.legacy_fingerprint = decode(%s, 'hex')
             ORDER BY a.name, a.id""",
        (household_id, identifier.kind, digest),
    )
    return _candidates(cur.fetchall())


def _suffix(
    cur: AccountCursor,
    household_id: UUID,
    institution_id: UUID,
    account_type: str,
    identifier: Identifier,
) -> tuple[AccountCandidate, ...]:
    cur.execute(
        """SELECT DISTINCT a.id, a.name, a.institution_id, a.account_type::text, a.currency,
                          ai.masked_value, ai.full_value IS NOT NULL
             FROM account a
             JOIN account_identifier ai
               ON ai.household_id = a.household_id AND ai.account_id = a.id
            WHERE a.household_id = %s AND a.is_active AND ai.is_active
              AND a.institution_id = %s AND a.account_type = %s::account_type
              AND ai.kind = %s
              AND COALESCE(ai.visible_suffix, right(ai.masked_value, 4)) = %s
            ORDER BY a.name, a.id""",
        (
            household_id,
            institution_id,
            account_type,
            identifier.kind,
            identifier.visible_suffix,
        ),
    )
    return _candidates(cur.fetchall())


def _conflicts(
    candidate: AccountCandidate,
    institution_id: UUID,
    account_type: str,
    currency: str | None,
) -> tuple[Literal["institution", "account_type", "currency"], ...]:
    conflicts: list[Literal["institution", "account_type", "currency"]] = []
    if candidate.institution_id != institution_id:
        conflicts.append("institution")
    if candidate.account_type != account_type:
        conflicts.append("account_type")
    if currency is not None and candidate.currency != currency:
        conflicts.append("currency")
    return tuple(conflicts)


def resolve_statement_account(
    cur: AccountCursor,
    *,
    household_id: UUID,
    institution: InstitutionResolution,
    account_type: str,
    currency: str | None,
    captured_value: str,
    source_is_masked: bool,
    legacy_salt: str | None = None,
) -> AccountResolution:
    """Resolve exact evidence first, then one institution/type/suffix fallback."""
    kind = "card" if account_type == "credit_card" else "bank_account"
    identifier = identifier_from_source(kind, captured_value, source_is_masked=source_is_masked)
    normalized_currency = currency.strip().upper() if currency and currency.strip() else None

    if institution.status != "matched":
        ambiguous = institution.status == "ambiguous"
        return AccountResolution(
            status="ambiguous" if ambiguous else "needs_confirmation",
            institution_action="choose" if ambiguous else "create",
            account_action="choose" if ambiguous else "create",
            account_number_masked=identifier.masked_value,
            candidates=(),
            identifier=identifier,
        )

    institution_id = institution.candidates[0].id
    if isinstance(identifier, CompleteIdentifier):
        exact = _exact(cur, household_id, identifier, legacy_salt)
        if exact:
            conflicts = _conflicts(exact[0], institution_id, account_type, normalized_currency)
            return AccountResolution(
                status="ambiguous" if len(exact) != 1 or conflicts else "matched",
                institution_action="reuse",
                account_action="choose" if len(exact) != 1 or conflicts else "reuse",
                account_number_masked=identifier.masked_value,
                candidates=exact,
                conflicts=conflicts,
                identifier=identifier,
            )

    suffix = _suffix(cur, household_id, institution_id, account_type, identifier)
    compatible = tuple(
        candidate
        for candidate in suffix
        if normalized_currency is None or candidate.currency == normalized_currency
    )
    if len(compatible) == 1:
        complete_source = isinstance(identifier, CompleteIdentifier)
        conflicting_complete = complete_source and compatible[0].can_reveal
        enriching = complete_source and not compatible[0].can_reveal
        return AccountResolution(
            status=(
                "ambiguous"
                if conflicting_complete
                else "needs_confirmation"
                if enriching
                else "matched"
            ),
            institution_action="reuse",
            account_action=(
                "choose" if conflicting_complete else "enrich" if enriching else "reuse"
            ),
            account_number_masked=identifier.masked_value,
            candidates=compatible,
            identifier=identifier,
        )
    if len(compatible) > 1 or suffix:
        fallback_conflicts: tuple[Literal["institution", "account_type", "currency"], ...] = (
            ("currency",) if suffix and not compatible else ()
        )
        return AccountResolution(
            status="ambiguous",
            institution_action="reuse",
            account_action="choose",
            account_number_masked=identifier.masked_value,
            candidates=compatible or suffix,
            conflicts=fallback_conflicts,
            identifier=identifier,
        )
    return AccountResolution(
        status="needs_confirmation",
        institution_action="reuse",
        account_action="create",
        account_number_masked=identifier.masked_value,
        candidates=(),
        identifier=identifier,
    )
