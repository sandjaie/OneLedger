"""Normalize local financial identifiers while keeping ordinary representations masked."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, cast

type IdentifierKind = Literal["bank_account", "iban", "card", "folio", "brokerage_account"]
_KINDS = frozenset({"bank_account", "iban", "card", "folio", "brokerage_account"})
_SEPARATORS = frozenset("-‐‑‒–—―−./٬٫")
_MASK_GLYPHS = frozenset("*•●…")
_MAX_SOURCE_LENGTH = 128
_MAX_SOURCE_BYTES = 512


class IdentifierValueError(ValueError):
    """A validation refusal that is safe to serialize because it never contains the input."""


class CompleteIdentifier:
    """A non-dataclass complete value that serializes only its safe presentation."""

    __slots__ = ("_full_value", "_kind", "_masked_value", "_normalized_value", "_visible_suffix")
    _kind: IdentifierKind
    _full_value: str
    _normalized_value: str
    _masked_value: str
    _visible_suffix: str

    def __init__(
        self,
        *,
        kind: IdentifierKind,
        full_value: str,
        normalized_value: str,
        masked_value: str,
        visible_suffix: str,
    ) -> None:
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_full_value", full_value)
        object.__setattr__(self, "_normalized_value", normalized_value)
        object.__setattr__(self, "_masked_value", masked_value)
        object.__setattr__(self, "_visible_suffix", visible_suffix)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CompleteIdentifier is immutable")

    @property
    def kind(self) -> IdentifierKind:
        return self._kind

    @property
    def full_value(self) -> str:
        return self._full_value

    @property
    def normalized_value(self) -> str:
        return self._normalized_value

    @property
    def masked_value(self) -> str:
        return self._masked_value

    @property
    def visible_suffix(self) -> str:
        return self._visible_suffix

    @property
    def can_reveal(self) -> bool:
        return True

    def __iter__(self) -> Iterator[tuple[str, object]]:
        """Let general encoders see only the ordinary masked representation."""
        yield "kind", self.kind
        yield "masked_value", self.masked_value
        yield "visible_suffix", self.visible_suffix
        yield "can_reveal", self.can_reveal

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CompleteIdentifier):
            return NotImplemented
        return (
            self.kind,
            self.full_value,
            self.normalized_value,
            self.masked_value,
            self.visible_suffix,
        ) == (
            other.kind,
            other.full_value,
            other.normalized_value,
            other.masked_value,
            other.visible_suffix,
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.kind,
                self.full_value,
                self.normalized_value,
                self.masked_value,
                self.visible_suffix,
            )
        )

    def __repr__(self) -> str:
        return (
            f"CompleteIdentifier(kind={self.kind!r}, masked_value={self.masked_value!r}, "
            f"visible_suffix={self.visible_suffix!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class MaskedIdentifier:
    """Evidence from a source that did not supply a recoverable complete value."""

    kind: IdentifierKind
    masked_value: str
    visible_suffix: str

    @property
    def can_reveal(self) -> bool:
        return False

    def __repr__(self) -> str:
        return (
            f"MaskedIdentifier(kind={self.kind!r}, masked_value={self.masked_value!r}, "
            f"visible_suffix={self.visible_suffix!r})"
        )


type Identifier = CompleteIdentifier | MaskedIdentifier


def _refuse() -> IdentifierValueError:
    return IdentifierValueError("the identifier does not match its declared kind")


def _kind(value: str) -> IdentifierKind:
    if value not in _KINDS:
        raise _refuse()
    return cast(IdentifierKind, value)


def _source(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise _refuse()
    source = value.strip()
    if (
        not source
        or len(source) > _MAX_SOURCE_LENGTH
        or len(source.encode("utf-8")) > _MAX_SOURCE_BYTES
        or any(unicodedata.category(character).startswith("C") for character in source)
    ):
        raise _refuse()
    return source, unicodedata.normalize("NFKC", source)


def _ascii_digit(character: str) -> str | None:
    if not character.isdecimal():
        return None
    try:
        return str(unicodedata.decimal(character))
    except ValueError:
        return None


def _is_separator(character: str) -> bool:
    return character.isspace() or character in _SEPARATORS


def _normalized_characters(value: str) -> str:
    normalized: list[str] = []
    for character in value:
        digit = _ascii_digit(character)
        if digit is not None:
            normalized.append(digit)
        elif _is_separator(character):
            continue
        elif character.isascii() and character.isalpha():
            normalized.append(character.upper())
        else:
            raise _refuse()
    return "".join(normalized)


def _valid_complete(kind: IdentifierKind, normalized: str) -> bool:
    if kind == "card":
        return 8 <= len(normalized) <= 19 and normalized.isascii() and normalized.isdigit()
    if kind == "iban":
        return (
            15 <= len(normalized) <= 34
            and normalized.isascii()
            and normalized.isalnum()
            and normalized[:2].isalpha()
            and normalized[2:4].isdigit()
        )
    return 5 <= len(normalized) <= 64 and normalized.isascii() and normalized.isalnum()


def complete_identifier(kind: str, value: str) -> CompleteIdentifier:
    """Build a locally stored complete identifier without reading keys or external state."""
    declared_kind = _kind(kind)
    source, prepared = _source(value)
    normalized = _normalized_characters(prepared)
    if not _valid_complete(declared_kind, normalized):
        raise _refuse()
    suffix = normalized[-4:]
    return CompleteIdentifier(
        kind=declared_kind,
        full_value=source,
        normalized_value=normalized,
        masked_value=f"****{suffix}",
        visible_suffix=suffix,
    )


def _visible_mask_characters(value: str) -> tuple[str, bool, str]:
    visible: list[str] = []
    trailing: list[str] = []
    has_mask = False
    for character in value:
        digit = _ascii_digit(character)
        if digit is not None:
            visible.append(digit)
            trailing.append(digit)
        elif character in _MASK_GLYPHS or character in "xX":
            has_mask = True
            trailing.clear()
        elif _is_separator(character):
            continue
        elif character.isascii() and character.isalpha():
            upper = character.upper()
            visible.append(upper)
            trailing.append(upper)
        else:
            raise _refuse()
    return "".join(visible), has_mask, "".join(trailing)


def masked_identifier(kind: str, value: str) -> MaskedIdentifier:
    """Retain only a safe suffix when the source does not supply a complete identifier."""
    declared_kind = _kind(kind)
    _, prepared = _source(value)
    visible, has_mask, trailing = _visible_mask_characters(prepared)
    if declared_kind == "card" and not visible.isdigit():
        raise _refuse()
    if has_mask:
        if len(trailing) < 4:
            raise _refuse()
        suffix = trailing[-4:]
    else:
        if len(visible) != 4:
            raise _refuse()
        suffix = visible
    return MaskedIdentifier(
        kind=declared_kind,
        masked_value=f"****{suffix}",
        visible_suffix=suffix,
    )


def identifier_from_source(kind: str, value: str, *, source_is_masked: bool) -> Identifier:
    """Build a declared capture without guessing whether legal X characters are masks."""
    if not isinstance(source_is_masked, bool):
        raise _refuse()
    if source_is_masked:
        return masked_identifier(kind, value)
    return complete_identifier(kind, value)
