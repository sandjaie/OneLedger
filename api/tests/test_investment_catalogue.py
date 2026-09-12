"""Catalogue contracts: public identity resolves exactly and price input stays exact."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from oneledger.identifiers import complete_identifier
from oneledger.routes import investment_catalogue as catalogue

FIRST = UUID("11111111-1111-1111-1111-111111111111")
SECOND = UUID("22222222-2222-2222-2222-222222222222")
HOUSEHOLD = UUID("33333333-3333-3333-3333-333333333333")


class Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, values: tuple[object, ...]) -> None:
        self.statements.append((statement, values))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def _row(identifier: UUID, *, name: str = "Synthetic Fund") -> tuple[object, ...]:
    return (
        identifier,
        None,
        "mutual_fund",
        name,
        "XIN",
        "XZ",
        "equity",
        None,
        "XSYN00000001",
        None,
        None,
    )


def test_exact_public_identity_resolves_one_visible_security() -> None:
    """Dropping the exact predicate would turn a deterministic import decision into a guess."""
    cursor = Cursor([_row(FIRST)])

    result = catalogue.resolve_security(cursor, HOUSEHOLD, isin="XSYN00000001")

    assert result.status == "matched"
    assert result.reason == "exact_public_identity"
    assert [candidate.id for candidate in result.candidates] == [FIRST]
    statement, values = cursor.statements[0]
    assert "s.household_id IS NULL OR s.household_id = %s" in statement
    assert "s.isin = %s" in statement
    assert values == (HOUSEHOLD, "XSYN00000001")


def test_name_only_ambiguity_abstains_without_echoing_the_name() -> None:
    """Choosing either same-named security would attach an event to the wrong instrument."""
    cursor = Cursor([_row(FIRST), _row(SECOND)])
    unsafe_name = "raw\u200b-name"

    result = catalogue.resolve_security(cursor, HOUSEHOLD, name=unsafe_name)

    assert result.status == "ambiguous"
    assert result.reason == "multiple_name_matches"
    assert len(result.candidates) == 2
    assert unsafe_name not in repr(result)


def test_manual_price_requires_a_decimal_string_not_a_float() -> None:
    """Accepting a JSON number would introduce IEEE-754 rounding before NUMERIC sees the price."""
    with pytest.raises(ValidationError):
        catalogue.ManualPriceCreate.model_validate({"priceDate": date(2026, 9, 1), "price": 12.5})
    with pytest.raises(ValidationError):
        catalogue.ManualPriceCreate.model_validate({"priceDate": date(2026, 9, 1), "price": "1e2"})
    body = catalogue.ManualPriceCreate.model_validate(
        {"priceDate": date(2026, 9, 1), "price": "12.500001"}
    )
    assert Decimal(body.price) == Decimal("12.500001")


def test_folio_serialization_keeps_unicode_source_and_normalized_value_private() -> None:
    """An ordinary response encoder must not reveal a complete investment identifier by accident."""
    source = "ＦＯＬ-１２３４５"
    value = complete_identifier("folio", source)

    encoded = jsonable_encoder(value)

    rendered = repr(encoded)
    assert source not in rendered
    assert value.normalized_value not in rendered
    assert encoded == {
        "kind": "folio",
        "masked_value": "****2345",
        "visible_suffix": "2345",
        "can_reveal": True,
    }
