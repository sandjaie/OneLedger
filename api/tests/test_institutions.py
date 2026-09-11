"""Statement issuer resolution is exact, scoped and willing to abstain."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from oneledger.institutions import normalize_institution_name, resolve_institution

FIRST = UUID("11111111-1111-1111-1111-111111111111")
SECOND = UUID("22222222-2222-2222-2222-222222222222")


class Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, statement: str, values: tuple[Any, ...]) -> None:
        self.statements.append((statement, values))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


def row(
    identifier: UUID,
    *,
    name: str = "Synthetic Bank",
    short_name: str | None = "Synthetic",
    country: str = "XZ",
    alias: str | None = None,
    alias_country: str | None = None,
) -> tuple[Any, ...]:
    return (identifier, name, short_name, country, "bank", alias, alias_country)


def test_institution_normalization_is_nfkc_casefolded_and_whitespace_stable() -> None:
    assert normalize_institution_name("  ＳＹＮＴＨＥＴＩＣ\u3000Bank\n PLC  ") == (
        "synthetic bank plc"
    )


@pytest.mark.parametrize("evidence", ["SYNTHETIC BANK", "synthetic", "Synthetic Banking Co"])
def test_canonical_short_and_alias_names_each_match_exactly(evidence: str) -> None:
    cursor = Cursor([row(FIRST, alias="Synthetic Banking Co", alias_country="XZ")])

    result = resolve_institution(cursor, UUID(int=99), evidence)

    assert result.status == "matched"
    assert [candidate.id for candidate in result.candidates] == [FIRST]
    assert result.proposed_name == evidence


def test_resolution_is_exact_and_does_not_use_a_substring() -> None:
    cursor = Cursor([row(FIRST, name="Synthetic Bank")])

    result = resolve_institution(cursor, UUID(int=99), "Synthetic Bank International")

    assert result.status == "needs_confirmation"
    assert result.candidates == ()


def test_country_evidence_scopes_equal_names_before_deciding() -> None:
    cursor = Cursor(
        [
            row(FIRST, country="XA"),
            row(SECOND, country="XB", short_name="Synthetic Two"),
        ]
    )

    matched = resolve_institution(cursor, UUID(int=99), "Synthetic Bank", country="XB")
    missing = resolve_institution(cursor, UUID(int=99), "Synthetic Bank", country="XC")

    assert matched.status == "matched"
    assert [candidate.id for candidate in matched.candidates] == [SECOND]
    assert missing.status == "needs_confirmation"
    assert missing.candidates == ()


def test_conflicting_scoped_matches_are_ambiguous_independent_of_row_order() -> None:
    rows = [row(FIRST), row(SECOND, short_name="Synthetic Two")]

    forwards = resolve_institution(Cursor(rows), UUID(int=99), "Synthetic Bank")
    backwards = resolve_institution(Cursor(list(reversed(rows))), UUID(int=99), "Synthetic Bank")

    assert forwards.status == backwards.status == "ambiguous"
    assert {candidate.id for candidate in forwards.candidates} == {FIRST, SECOND}
    assert forwards.candidates == backwards.candidates


def test_multiple_matching_spellings_for_one_uuid_are_one_candidate() -> None:
    cursor = Cursor(
        [
            row(FIRST, alias="Synthetic Bank", alias_country="XZ"),
            row(FIRST, alias="SYNTHETIC BANK", alias_country="XZ"),
        ]
    )

    result = resolve_institution(cursor, UUID(int=99), "synthetic bank")

    assert result.status == "matched"
    assert len(result.candidates) == 1


def test_query_reads_only_visible_shared_or_household_rows_and_never_writes() -> None:
    household_id = UUID(int=99)
    cursor = Cursor([])

    result = resolve_institution(cursor, household_id, "Unseen Institution")

    assert result.status == "needs_confirmation"
    assert len(cursor.statements) == 1
    statement, values = cursor.statements[0]
    assert statement.lstrip().startswith("SELECT")
    assert "i.household_id IS NULL OR i.household_id = %s" in statement
    assert "ia.household_id IS NULL OR ia.household_id = %s" in statement
    assert values == (household_id, household_id)
