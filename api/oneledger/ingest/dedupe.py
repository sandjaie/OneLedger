"""M2 Task 4b: the transaction fingerprint and the near-duplicate warning.

Layers 2 and 3 of the specification's section 4.7. Layer 1 — the document checksum — belongs to
the module that owns the blob, not here.

Pure functions: no database and no network. The caller supplies the committed rows to compare
against, which keeps the whole of dedupe testable on values and leaves the one connection in the
pipeline where the module layout puts it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from oneledger.ingest.normalize import NormalizedRow

STATUS_NEW = "new"
STATUS_DUPLICATE = "duplicate"
STATUS_NEAR_DUPLICATE = "near_duplicate"

# Layer 3's window and threshold, per section 4.7. A near-duplicate warns and never blocks: it
# catches a genuinely double-charged subscription as readily as a re-import, and only the owner
# can tell those apart.
LEDGER_PLACES = Decimal("0.0001")

NEAR_DUPLICATE_DAYS = 3
NEAR_DUPLICATE_SIMILARITY = 0.85

_WHITESPACE = re.compile(r"\s+")

# Combining marks and format characters carry no token boundary: deleted, never spaced.
_INVISIBLE = frozenset({"Mn", "Mc", "Me", "Cf"})

# The two disambiguators are drawn from different worlds — one is bank text, the other a counter —
# so they carry distinct prefixes. Without them a reference of "1" and an occurrence index of 1
# produce one fingerprint for two distinct rows, which V10's unique index then rejects at commit,
# or which marks one row a false duplicate of the other. The repeat counter is appended to every
# referenced row, not only a repeated one, so that a literal reference "ABC#1" cannot collide with
# the "ABC" that repeated once: splitting on the last "#" always separates the two.
_REFERENCE = "ref:"
# Public because `routes/ingest.py`'s confirmed-distinct override recomputes in this namespace
# (spec §7 item 12), and a second spelling of it there is the collision the prefix exists to
# prevent.
OCCURRENCE = "occ:"


@dataclass(frozen=True)
class CommittedRow:
    """A row already in `transaction`, in the shape both layers need to compare against.

    Committed, not staged: V13's `duplicate_of_transaction_id` and `near_duplicate_of_id` both
    reference `transaction(id)`, so a staged row is not something either verdict can name.

    `account_id` is carried so layer 3's "same account" condition is enforced here rather than
    assumed of the caller — a wider candidate set must not produce a cross-account warning.
    """

    id: UUID
    account_id: UUID
    dedupe_hash: str
    booked_date: date
    amount: Decimal
    description_raw: str


@dataclass(frozen=True)
class DedupeVerdict:
    """One verdict per normalized row, in the order the rows were given.

    `occurrence_index` is the row's position among identical `(date, amount, normalised
    description)` rows in this document, counted from 0. It is reported for every row — including
    one a reference disambiguated — because the confirmed-distinct override advances it, and
    because a row that did not consume an index would shift every later one.
    """

    dedupe_hash: str
    status: str
    duplicate_of_transaction_id: UUID | None
    near_duplicate_of_id: UUID | None
    occurrence_index: int


def normalise_for_hash(description: str) -> str:
    """Uppercase, strip punctuation, collapse whitespace. Reads; never writes back.

    Punctuation is anything that is not a letter or a digit **in any script**, replaced by a space
    rather than deleted: "A.B" is two tokens and closing the gap would silently make it one. An
    ASCII-only rule would erase whole descriptions — a Cyrillic, Arabic or CJK merchant name would
    normalise to the empty string, and two unrelated shops charging the same amount on the same day
    would fingerprint identically and the second would be marked a duplicate of the first. No rule
    here may be specific to one script (ADR-0012).

    The text is put in canonical form first, and combining marks and zero-width formatters are
    deleted rather than spaced. Extraction yields precomposed or decomposed text depending on the
    font's ToUnicode map, so one merchant reaches this function both ways: without NFKC the two
    forms hash differently *and* score 0.0 against each other, because the mark splits a word in
    two — so layer 2 misses the overlap and layer 3 does not catch it either, and the overlapping
    month imports twice. A soft hyphen from line-break hyphenation does the same.
    """
    # Normalised, uppercased, then normalised again: uppercasing expands some letters into a
    # base plus a combining mark (Greek "Μαΐου" is one), and a mark introduced after the only
    # NFKC pass would be deleted uncomposed — so a case-only variant would hash differently and
    # score 0.0 against its own lowercase form.
    composed = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", description).upper())
    kept = []
    for ch in composed:
        if unicodedata.category(ch) in _INVISIBLE:
            continue
        kept.append(ch if (ch.isalnum() or ch.isspace()) else " ")
    return _WHITESPACE.sub(" ", "".join(kept)).strip()


def fingerprint(
    *,
    account_id: UUID,
    booked_date: date,
    amount: Decimal,
    description_raw: str,
    disambiguator: str,
) -> str:
    """Section 4.7 layer 2, exactly.

    The amount is quantized to the four places the ledger stores, so "-310" and "-310.0000" are
    one transaction and cannot produce two hashes.
    """
    return hashlib.sha256(
        "|".join(
            [
                str(account_id),
                booked_date.isoformat(),
                _hashed_amount(amount),
                normalise_for_hash(description_raw),
                disambiguator,
            ]
        ).encode()
    ).hexdigest()


def _hashed_amount(amount: Decimal) -> str:
    """Four places, rounding half away from zero — the rule Postgres applies to NUMERIC(20,4).

    Normalization already quantizes, so this is belt and braces; the point is that the hash can
    never round by a different rule than the column it will be compared against.
    """
    return str(amount.quantize(LEDGER_PLACES, rounding=ROUND_HALF_UP))


def token_set_ratio(left: str, right: str) -> float:
    """Sørensen–Dice over token sets: order-insensitive, no new dependency.

    Two empty descriptions are identical rather than incomparable; one empty is not a match.
    """
    first, second = set(left.split()), set(right.split())
    if not first and not second:
        return 1.0
    if not first or not second:
        return 0.0
    return 2 * len(first & second) / (len(first) + len(second))


def classify(
    rows: list[NormalizedRow],
    *,
    account_id: UUID,
    maps_reference: bool,
    committed: list[CommittedRow],
) -> list[DedupeVerdict]:
    """Fingerprint each row and classify it against the rows already committed."""
    by_hash = {row.dedupe_hash: row for row in committed}
    candidates = [(other, normalise_for_hash(other.description_raw)) for other in committed]
    occurrences: dict[tuple[date, str, str], int] = {}
    repeats: dict[tuple[date, str, str, str], int] = {}
    verdicts: list[DedupeVerdict] = []

    for row in rows:
        normalised = normalise_for_hash(row.description_raw)
        reference = (row.reference_raw or "").strip() if maps_reference else ""

        # Counted among *identical* rows within this document only — never a row ordinal, and
        # never offset by committed rows. A statement is a stable, self-contained ordering of its
        # day, so an overlapping month reproduces these indices and the hashes collide, which is
        # how the overlap is recognised. Offsetting would import August twice.
        #
        # The counter advances for **every** row, including one a reference disambiguates. If a
        # referenced row did not consume its index, a reference present in one statement and blank
        # in the next would shift every later index by one, and each row would then be marked a
        # duplicate of the wrong transaction — a stored fact under I7, stored wrong.
        key = (row.booked_date, _hashed_amount(row.amount), normalised)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1

        if reference:
            # A reference separates two identical-looking transactions only while it is unique.
            # Some banks fill the column with a constant, and three distinct coffees would then
            # share one fingerprint, stage as clean, and be rejected wholesale by V10's unique
            # index at commit — after review, with nothing left behind to diagnose (spec 5.5).
            seen = repeats.get((*key, reference), 0)
            repeats[(*key, reference)] = seen + 1
            disambiguator = f"{_REFERENCE}{reference}#{seen}"
        else:
            disambiguator = f"{OCCURRENCE}{occurrence}"

        digest = fingerprint(
            account_id=account_id,
            booked_date=row.booked_date,
            amount=row.amount,
            description_raw=row.description_raw,
            disambiguator=disambiguator,
        )

        exact = by_hash.get(digest)
        if exact is not None:
            verdicts.append(DedupeVerdict(digest, STATUS_DUPLICATE, exact.id, None, occurrence))
            continue

        near = _nearest(row, normalised, account_id, candidates)
        if near is not None:
            verdicts.append(DedupeVerdict(digest, STATUS_NEAR_DUPLICATE, None, near.id, occurrence))
            continue

        verdicts.append(DedupeVerdict(digest, STATUS_NEW, None, None, occurrence))

    return verdicts


def _nearest(
    row: NormalizedRow,
    normalised: str,
    account_id: UUID,
    candidates: list[tuple[CommittedRow, str]],
) -> CommittedRow | None:
    """Layer 3: the closest committed row within the window, or none.

    The best match wins rather than the first, and ties break on the smaller date gap and then on
    the id, so the verdict does not depend on the order the caller happened to supply.
    """
    best: tuple[float, int, str, CommittedRow] | None = None
    for other, other_normalised in candidates:
        # Amount first: it rejects most candidates outright, and the comparison is free.
        if other.amount != row.amount or other.account_id != account_id:
            continue
        gap = abs((other.booked_date - row.booked_date).days)
        if gap > NEAR_DUPLICATE_DAYS:
            continue
        similarity = token_set_ratio(normalised, other_normalised)
        if similarity < NEAR_DUPLICATE_SIMILARITY:
            continue
        candidate = (-similarity, gap, str(other.id), other)
        if best is None or candidate[:3] < best[:3]:
            best = candidate
    return best[3] if best is not None else None
