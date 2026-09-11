"""Typed local financial identifiers never need an application encryption key."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.encoders import jsonable_encoder

from oneledger.identifiers import (
    CompleteIdentifier,
    IdentifierValueError,
    MaskedIdentifier,
    complete_identifier,
    identifier_from_source,
    masked_identifier,
)

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("relative_path", "obsolete_contract"),
    [
        ("docs/product/prd.html", "a hash of the full number for statement matching"),
        (
            "docs/design/architecture.html",
            "Rotated — it would invalidate every stored hash",
        ),
        (
            "docs/operations/security-and-privacy.html",
            "re-hashing every account number",
        ),
        (
            "web/app/import/import-screen.tsx",
            "statement's own number by salted hash",
        ),
        (
            "api/oneledger/routes/ingest.py",
            "statement's own account number is never stored",
        ),
        (
            "api/oneledger/ingest/pipeline.py",
            "the hashing that lookup needs stays in the route",
        ),
    ],
)
def test_active_contract_sources_do_not_describe_the_retired_hash_only_identity_model(
    relative_path: str,
    obsolete_contract: str,
) -> None:
    text = " ".join((ROOT / relative_path).read_text().split())

    assert obsolete_contract not in text


@pytest.mark.parametrize(
    ("kind", "source", "normalized", "suffix"),
    [
        ("bank_account", " 00٧٢-34/56.00 ", "0072345600", "5600"),
        ("card", "٤١١١ 1111‑1111.1111", "4111111111111111", "1111"),
        ("iban", "gb82 west 1234 5698 7654 32", "GB82WEST12345698765432", "5432"),
    ],
)
def test_complete_identifiers_retain_source_and_normalize_by_kind(
    kind: str, source: str, normalized: str, suffix: str
) -> None:
    value = complete_identifier(kind, source)

    assert isinstance(value, CompleteIdentifier)
    assert value.kind == kind
    assert value.full_value == source.strip()
    assert value.normalized_value == normalized
    assert value.visible_suffix == suffix
    assert value.masked_value == f"****{suffix}"
    assert value.can_reveal is True


def test_normalization_is_stable_and_has_no_environment_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACCOUNT_HASH_SALT", raising=False)
    before = dict(os.environ)

    first = complete_identifier("bank_account", "AB-12 34 56")
    second = complete_identifier("bank_account", "ab12.34/56")

    assert first.normalized_value == second.normalized_value == "AB123456"
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    ("kind", "source", "suffix"),
    [
        ("bank_account", "XXXX XXXX 9812", "9812"),
        ("card", "••••-••••-••••-4242", "4242"),
        ("card", "••••-••••-••••-٤٢٤٢", "4242"),
        ("iban", "GB82 **** **** **** 5432", "5432"),
        ("bank_account", "9812", "9812"),
    ],
)
def test_masked_identifiers_keep_only_safe_matching_evidence(
    kind: str, source: str, suffix: str
) -> None:
    value = masked_identifier(kind, source)

    assert isinstance(value, MaskedIdentifier)
    assert value.kind == kind
    assert value.masked_value == f"****{suffix}"
    assert value.visible_suffix == suffix
    assert value.can_reveal is False
    assert not hasattr(value, "full_value")
    assert not hasattr(value, "normalized_value")


def test_statement_source_classification_is_explicit() -> None:
    assert isinstance(
        identifier_from_source("bank_account", "12345678", source_is_masked=False),
        CompleteIdentifier,
    )
    assert isinstance(
        identifier_from_source("bank_account", "****5678", source_is_masked=True),
        MaskedIdentifier,
    )
    assert isinstance(
        identifier_from_source("bank_account", "5678", source_is_masked=True),
        MaskedIdentifier,
    )


@pytest.mark.parametrize(
    ("kind", "source", "normalized"),
    [
        ("bank_account", "AXX-123456", "AXX123456"),
        ("iban", "GB54 WEST XX34 5698 7654 32", "GB54WESTXX345698765432"),
    ],
)
def test_literal_x_in_a_complete_alphanumeric_identifier_is_not_a_mask(
    kind: str, source: str, normalized: str
) -> None:
    value = identifier_from_source(kind, source, source_is_masked=False)

    assert isinstance(value, CompleteIdentifier)
    assert value.full_value == source
    assert value.normalized_value == normalized


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        ("card", "4111_1111_1111_1111"),
        ("card", "4111A11111111111"),
        ("card", "1234567"),
        ("bank_account", "AB_123456"),
        ("bank_account", "12\u200b345678"),
        ("iban", "GB8A WEST 1234 5698 7654 32"),
        ("iban", "GB82 WEST 1234 5698 7654 3212 3456 7890 123"),
        ("unknown", "12345678"),
        ("card", ""),
    ],
)
def test_complete_identifier_validation_refuses_malformed_values_without_echoing(
    kind: str, source: str
) -> None:
    with pytest.raises(IdentifierValueError) as refusal:
        complete_identifier(kind, source)

    if source:
        assert source not in str(refusal.value)
    assert str(refusal.value) == "the identifier does not match its declared kind"


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        ("card", "4111 1111 1111 1111"),
        ("bank_account", "12345"),
        ("bank_account", "****12"),
        ("iban", "**** A$CD"),
        ("card", "****ABCD"),
        ("card", "ABCD"),
        ("card", "4242 ****"),
        ("card", "42 **** 42"),
        ("unknown", "****1234"),
    ],
)
def test_masked_identifier_validation_refuses_unsafe_or_unusable_values(
    kind: str, source: str
) -> None:
    with pytest.raises(IdentifierValueError) as refusal:
        masked_identifier(kind, source)

    assert source not in str(refusal.value)
    assert str(refusal.value) == "the identifier does not match its declared kind"


def test_complete_constructor_refuses_a_source_mask() -> None:
    for source in ("****1234", "•••• 1234"):
        with pytest.raises(IdentifierValueError):
            complete_identifier("bank_account", source)


def test_complete_value_repr_and_str_never_contain_the_identifier() -> None:
    source = "4111 1111 1111 1111"
    value = complete_identifier("card", source)

    assert source not in repr(value)
    assert value.normalized_value not in repr(value)
    assert source not in str(value)
    assert value.normalized_value not in str(value)
    assert repr(value) == (
        "CompleteIdentifier(kind='card', masked_value='****1111', visible_suffix='1111')"
    )


def test_complete_value_ordinary_serialization_is_safe() -> None:
    source = "٤١١١ 1111‑1111.1111"
    normalized = "4111111111111111"
    value = complete_identifier("card", source)

    with pytest.raises(TypeError):
        asdict(cast(Any, value))

    encoded = jsonable_encoder(value)

    assert encoded == {
        "kind": "card",
        "masked_value": "****1111",
        "visible_suffix": "1111",
        "can_reveal": True,
    }
    assert source not in str(encoded)
    assert normalized not in str(encoded)


def test_masked_value_repr_contains_only_the_safe_mask() -> None:
    value = masked_identifier("card", "**** **** **** 4242")

    assert repr(value) == (
        "MaskedIdentifier(kind='card', masked_value='****4242', visible_suffix='4242')"
    )


def test_source_spelling_and_normalized_value_are_bounded() -> None:
    for source in ("1" * 129, "1 " * 129):
        with pytest.raises(IdentifierValueError):
            complete_identifier("bank_account", source)
