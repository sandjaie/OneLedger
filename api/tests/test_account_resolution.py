"""Statement identity resolution over invented account and institution evidence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from oneledger.account_resolution import AccountResolution, resolve_statement_account
from oneledger.institutions import InstitutionCandidate, InstitutionResolution

HOUSEHOLD = UUID("11111111-1111-1111-1111-111111111111")
INSTITUTION = UUID("22222222-2222-2222-2222-222222222222")
ACCOUNT = UUID("33333333-3333-3333-3333-333333333333")


def institution(*, status: str = "matched") -> InstitutionResolution:
    candidate = InstitutionCandidate(
        id=INSTITUTION,
        name="Example Institution",
        short_name="Example",
        country="ZZ",
        institution_type="bank",
    )
    return InstitutionResolution(
        status=status,  # type: ignore[arg-type]
        proposed_name="Example Institution",
        country="ZZ",
        candidates=(candidate,) if status == "matched" else (),
    )


class Cursor:
    def __init__(
        self,
        *,
        exact: list[tuple[Any, ...]] | None = None,
        legacy: list[tuple[Any, ...]] | None = None,
        suffix: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.rows = {
            "normalized_value": exact or [],
            "legacy_fingerprint": legacy or [],
            "visible_suffix": suffix or [],
        }
        self.selected: list[tuple[Any, ...]] = []

    def execute(self, statement: str, _values: tuple[Any, ...]) -> None:
        self.selected = next(
            (rows for marker, rows in self.rows.items() if marker in statement), []
        )

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.selected


def row(
    *,
    account: UUID = ACCOUNT,
    institution_id: UUID = INSTITUTION,
    account_type: str = "savings",
    currency: str = "AED",
    mask: str = "****3579",
    complete: bool = True,
) -> tuple[Any, ...]:
    return (account, "Example savings", institution_id, account_type, currency, mask, complete)


def resolve(
    cur: Cursor, value: str, *, masked: bool = False, account_type: str = "savings"
) -> AccountResolution:
    return resolve_statement_account(
        cur,
        household_id=HOUSEHOLD,
        institution=institution(),
        account_type=account_type,
        currency="AED",
        captured_value=value,
        source_is_masked=masked,
        legacy_salt="legacy-test-salt",
    )


def test_complete_identity_reuses_one_exact_active_account() -> None:
    result = resolve(Cursor(exact=[row()]), "2468013579")

    assert result.status == "matched"
    assert result.institution_action == "reuse"
    assert result.account_action == "reuse"
    assert [candidate.id for candidate in result.candidates] == [ACCOUNT]
    assert repr(result).find("2468013579") == -1


def test_complete_identity_temporarily_reuses_one_legacy_digest() -> None:
    result = resolve(Cursor(legacy=[row(complete=False)]), "2468013579")

    assert result.status == "matched"
    assert result.account_action == "reuse"


def test_complete_identity_enriches_one_compatible_mask_only_account() -> None:
    result = resolve(Cursor(suffix=[row(complete=False)]), "2468013579")

    assert result.status == "needs_confirmation"
    assert result.account_action == "enrich"
    assert [candidate.account_number_masked for candidate in result.candidates] == ["****3579"]


def test_complete_identity_never_matches_a_different_complete_value_by_suffix() -> None:
    result = resolve(Cursor(suffix=[row(complete=True)]), "2468013579")

    assert result.status == "ambiguous"
    assert result.account_action == "choose"


def test_mask_fallback_reuses_only_one_compatible_account() -> None:
    result = resolve(Cursor(suffix=[row()]), "****3579", masked=True)

    assert result.status == "matched"
    assert result.account_action == "reuse"


def test_mask_fallback_proposes_a_distinct_account_when_none_match() -> None:
    result = resolve(Cursor(), "****3579", masked=True, account_type="current")

    assert result.status == "needs_confirmation"
    assert result.institution_action == "reuse"
    assert result.account_action == "create"
    assert result.candidates == ()


def test_mask_fallback_asks_when_more_than_one_account_matches() -> None:
    result = resolve(
        Cursor(suffix=[row(), row(account=UUID("44444444-4444-4444-4444-444444444444"))]),
        "****3579",
        masked=True,
    )

    assert result.status == "ambiguous"
    assert result.account_action == "choose"
    assert len(result.candidates) == 2


def test_exact_identity_with_conflicting_account_facts_never_matches() -> None:
    result = resolve(Cursor(exact=[row(institution_id=UUID(int=9), currency="USD")]), "2468013579")

    assert result.status == "ambiguous"
    assert result.account_action == "choose"
    assert result.conflicts == ("institution", "currency")


def test_unknown_institution_proposes_institution_and_account_without_querying_accounts() -> None:
    cur = Cursor(exact=[row()])
    result = resolve_statement_account(
        cur,
        household_id=HOUSEHOLD,
        institution=institution(status="needs_confirmation"),
        account_type="savings",
        currency="AED",
        captured_value="2468013579",
        source_is_masked=False,
        legacy_salt="legacy-test-salt",
    )

    assert result.status == "needs_confirmation"
    assert result.institution_action == "create"
    assert result.account_action == "create"
    assert result.candidates == ()


def test_unicode_formatted_source_is_normalized_but_only_its_ascii_mask_is_presented() -> None:
    source = "٢٤٦٨-٠١٣٥٧٩"

    result = resolve(Cursor(), source)

    assert result.account_number_masked == "****3579"
    assert source not in repr(result)
