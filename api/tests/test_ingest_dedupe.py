"""M2 Task 4b: dedupe — the transaction fingerprint and the near-duplicate warning.

Pure values, no database and no network: the caller supplies the committed rows to compare
against. The spec's section 4.7 layers 2 and 3 are the contract; layer 1, the document checksum,
belongs to Task 5c. Every trap the spec names is asserted here rather than assumed — above all
that the occurrence index is counted **within this document only**, which is what lets an
overlapping month's rows collide with their committed copies instead of importing twice.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from oneledger.ingest import dedupe
from oneledger.ingest.dedupe import CommittedRow
from oneledger.ingest.normalize import NormalizedRow

ACCOUNT = UUID("11111111-1111-4111-8111-111111111111")
CURRENCY = "XAA"


def nrow(
    number: int,
    *,
    booked_date: date = date(2026, 3, 4),
    amount: str = "-310.0000",
    description: str = "COFFEE SHOP",
    reference: str | None = None,
) -> NormalizedRow:
    return NormalizedRow(
        source_page_number=1,
        source_row_number=number,
        raw_row={},
        booked_date=booked_date,
        value_date=None,
        description_raw=description,
        description_clean=description,
        description_rules_applied=(),
        reference_raw=reference,
        amount=Decimal(amount),
        currency=CURRENCY,
        original_amount=None,
        original_currency=None,
        proposed_txn_type="expense",
        running_balance=None,
    )


def committed(
    row: NormalizedRow, *, occurrence: int = 0, txn_id: UUID | None = None
) -> CommittedRow:
    """A row already in `transaction`, carrying the hash this module would have given it."""
    return CommittedRow(
        id=txn_id or uuid4(),
        account_id=ACCOUNT,
        dedupe_hash=dedupe.fingerprint(
            account_id=ACCOUNT,
            booked_date=row.booked_date,
            amount=row.amount,
            description_raw=row.description_raw,
            disambiguator=str(occurrence),
        ),
        booked_date=row.booked_date,
        amount=row.amount,
        description_raw=row.description_raw,
    )


def classify(
    rows: list[NormalizedRow],
    *,
    maps_reference: bool = False,
    prior: list[CommittedRow] | None = None,
) -> list[dedupe.DedupeVerdict]:
    return dedupe.classify(
        rows, account_id=ACCOUNT, maps_reference=maps_reference, committed=prior or []
    )


# --- normalise_for_hash ----------------------------------------------------


def test_normalise_for_hash_ignores_case_whitespace_and_punctuation() -> None:
    assert dedupe.normalise_for_hash("Coffee   Shop, Ltd.") == dedupe.normalise_for_hash(
        "COFFEE SHOP LTD"
    )
    assert dedupe.normalise_for_hash("  padded  ") == dedupe.normalise_for_hash("PADDED")


def test_descriptions_differing_only_in_noise_fingerprint_identically() -> None:
    quiet = dedupe.fingerprint(
        account_id=ACCOUNT,
        booked_date=date(2026, 3, 4),
        amount=Decimal("-310.0000"),
        description_raw="Coffee Shop",
        disambiguator="0",
    )
    noisy = dedupe.fingerprint(
        account_id=ACCOUNT,
        booked_date=date(2026, 3, 4),
        amount=Decimal("-310.0000"),
        description_raw="  COFFEE,  SHOP.  ",
        disambiguator="0",
    )
    assert quiet == noisy


def test_the_fingerprint_is_stable_and_is_sha256_hex() -> None:
    def hashed() -> str:
        return dedupe.fingerprint(
            account_id=ACCOUNT,
            booked_date=date(2026, 3, 4),
            amount=Decimal("-310.0000"),
            description_raw="COFFEE SHOP",
            disambiguator="0",
        )

    first = hashed()
    assert first == hashed()
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_the_amount_is_hashed_at_four_decimal_places() -> None:
    def hashed(amount: str) -> str:
        return dedupe.fingerprint(
            account_id=ACCOUNT,
            booked_date=date(2026, 3, 4),
            amount=Decimal(amount),
            description_raw="COFFEE SHOP",
            disambiguator="0",
        )

    # The ledger stores NUMERIC(20,4); "-310" and "-310.0000" are the same stored amount and
    # must not produce two hashes for one transaction.
    assert hashed("-310") == hashed("-310.0000")
    assert hashed("-310.0000") != hashed("-310.0001")


# --- the occurrence index --------------------------------------------------


def test_identical_rows_in_one_document_are_numbered_from_zero() -> None:
    verdicts = classify([nrow(1), nrow(2)])
    assert [v.occurrence_index for v in verdicts] == [0, 1]
    assert verdicts[0].dedupe_hash != verdicts[1].dedupe_hash


def test_the_occurrence_index_is_not_a_row_ordinal() -> None:
    """Counted among *identical* rows, not among the document's rows."""
    rows = [
        nrow(1, description="COFFEE SHOP"),
        nrow(2, description="BOOKSHOP"),
        nrow(3, description="COFFEE SHOP"),
    ]
    verdicts = classify(rows)
    assert [v.occurrence_index for v in verdicts] == [0, 0, 1]


def test_an_overlapping_statement_reproduces_the_committed_hashes() -> None:
    """The reason the index is never offset by committed rows.

    July–August stages two identical coffees and they commit. August–September carries the same
    two rows plus a new one. Restarting at 0 makes the overlap collide and be recognised; an
    offset by the two committed rows would hand them indices 2 and 3, and August would import
    twice. This test fails under any offsetting scheme.
    """
    july = [nrow(1), nrow(2)]
    already = [committed(july[0], occurrence=0), committed(july[1], occurrence=1)]

    august = [nrow(1), nrow(2), nrow(3, description="BOOKSHOP")]
    verdicts = classify(august, prior=already)

    assert [v.status for v in verdicts] == ["duplicate", "duplicate", "new"]
    assert [v.dedupe_hash for v in verdicts[:2]] == [r.dedupe_hash for r in already]
    assert verdicts[0].duplicate_of_transaction_id == already[0].id
    assert verdicts[1].duplicate_of_transaction_id == already[1].id


# --- the reference disambiguator -------------------------------------------


def test_a_mapped_reference_disambiguates_instead_of_the_index() -> None:
    rows = [nrow(1, reference="REF-A"), nrow(2, reference="REF-B")]
    verdicts = classify(rows, maps_reference=True)
    assert [v.occurrence_index for v in verdicts] == [None, None]
    assert verdicts[0].dedupe_hash != verdicts[1].dedupe_hash

    # Two rows sharing a reference are the same transaction as far as the fingerprint is
    # concerned; the index is not silently reintroduced to separate them.
    same = classify([nrow(1, reference="REF-A"), nrow(2, reference="REF-A")], maps_reference=True)
    assert same[0].dedupe_hash == same[1].dedupe_hash


def test_an_empty_reference_falls_back_to_the_occurrence_index() -> None:
    rows = [nrow(1, reference="   "), nrow(2, reference=None)]
    verdicts = classify(rows, maps_reference=True)
    assert [v.occurrence_index for v in verdicts] == [0, 1]
    assert verdicts[0].dedupe_hash != verdicts[1].dedupe_hash


def test_a_reference_is_ignored_when_the_profile_maps_no_reference_column() -> None:
    rows = [nrow(1, reference="REF-A"), nrow(2, reference="REF-B")]
    verdicts = classify(rows, maps_reference=False)
    assert [v.occurrence_index for v in verdicts] == [0, 1]


# --- layer 2: the duplicate ------------------------------------------------


def test_a_row_matching_a_committed_hash_is_marked_duplicate_and_names_it() -> None:
    row = nrow(1)
    already = committed(row)
    [verdict] = classify([row], prior=[already])
    assert verdict.status == "duplicate"
    assert verdict.duplicate_of_transaction_id == already.id
    assert verdict.near_duplicate_of_id is None


def test_a_row_with_no_committed_match_is_new() -> None:
    [verdict] = classify([nrow(1)], prior=[committed(nrow(1, description="BOOKSHOP"))])
    assert verdict.status == "new"
    assert verdict.duplicate_of_transaction_id is None


# --- layer 3: the near-duplicate warning -----------------------------------


def test_a_near_duplicate_is_flagged_as_a_stored_fact() -> None:
    """Same account and amount, within three days, description close enough."""
    earlier = committed(nrow(1, booked_date=date(2026, 3, 2), description="COFFEE SHOP LONDON"))
    [verdict] = classify([nrow(1, description="COFFEE SHOP, LONDON")], prior=[earlier])
    assert verdict.status == "near_duplicate"
    assert verdict.near_duplicate_of_id == earlier.id
    assert verdict.duplicate_of_transaction_id is None


def test_a_near_duplicate_outside_three_days_is_not_flagged() -> None:
    earlier = committed(nrow(1, booked_date=date(2026, 2, 25), description="COFFEE SHOP LONDON"))
    [verdict] = classify([nrow(1, description="COFFEE SHOP LONDON")], prior=[earlier])
    assert verdict.status == "new"


def test_a_near_duplicate_below_the_similarity_threshold_is_not_flagged() -> None:
    earlier = committed(nrow(1, booked_date=date(2026, 3, 3), description="AIRLINE TICKET REFUND"))
    [verdict] = classify([nrow(1, description="COFFEE SHOP")], prior=[earlier])
    assert verdict.status == "new"


def test_a_different_amount_is_never_a_near_duplicate() -> None:
    earlier = committed(nrow(1, booked_date=date(2026, 3, 3), amount="-99.0000"))
    [verdict] = classify([nrow(1)], prior=[earlier])
    assert verdict.status == "new"


def test_an_exact_duplicate_is_not_downgraded_to_a_near_duplicate() -> None:
    """A row can satisfy both layers; the more specific verdict wins."""
    row = nrow(1)
    exact = committed(row)
    [verdict] = classify([row], prior=[exact])
    assert verdict.status == "duplicate"
    assert verdict.near_duplicate_of_id is None


def test_the_token_set_ratio_ignores_token_order() -> None:
    assert dedupe.token_set_ratio("COFFEE SHOP LONDON", "LONDON SHOP COFFEE") == 1.0
    assert dedupe.token_set_ratio("COFFEE SHOP", "COFFEE SHOP") == 1.0
    assert dedupe.token_set_ratio("COFFEE SHOP", "AIRLINE TICKET") < 0.85


# --- scripts other than Latin ----------------------------------------------


def test_normalisation_keeps_letters_of_every_script() -> None:
    """Punctuation is stripped; alphabets are not.

    An ASCII-only rule erased whole descriptions, so two unrelated Cyrillic, Arabic or CJK
    merchants normalised to the empty string, fingerprinted identically, and the second was
    marked a duplicate of the first.
    """
    for description in ("КОФЕЙНЯ", "مقهى", "喫茶店", "MÜLLER", "CAFÉ ROMA"):
        assert dedupe.normalise_for_hash(description) != ""

    assert dedupe.normalise_for_hash("КОФЕЙНЯ") != dedupe.normalise_for_hash("МАГАЗИН")
    assert dedupe.normalise_for_hash("MÜLLER") != dedupe.normalise_for_hash("MULLER")
    # Punctuation still goes, in any script.
    assert dedupe.normalise_for_hash("КОФЕЙНЯ, ООО.") == dedupe.normalise_for_hash("КОФЕЙНЯ ООО")


def test_two_non_latin_merchants_are_not_duplicates_of_each_other() -> None:
    rows = [nrow(1, description="КОФЕЙНЯ"), nrow(2, description="МАГАЗИН")]
    verdicts = classify(rows, prior=[committed(rows[0])])
    assert [v.status for v in verdicts] == ["duplicate", "new"]
    assert verdicts[0].dedupe_hash != verdicts[1].dedupe_hash


# --- the caller's candidate set --------------------------------------------


def test_a_committed_row_on_another_account_is_never_a_near_duplicate() -> None:
    """Layer 3's "same account" condition is enforced here, not assumed of the caller."""
    other_account = committed(nrow(1, booked_date=date(2026, 3, 3)))
    elsewhere = CommittedRow(
        id=other_account.id,
        account_id=UUID("22222222-2222-4222-8222-222222222222"),
        dedupe_hash=other_account.dedupe_hash,
        booked_date=other_account.booked_date,
        amount=other_account.amount,
        description_raw=other_account.description_raw,
    )
    [verdict] = classify([nrow(1)], prior=[elsewhere])
    assert verdict.status == "new"


def test_no_rows_yields_no_verdicts() -> None:
    assert classify([]) == []


def test_the_hashed_amount_rounds_half_away_from_zero() -> None:
    """The rule Postgres applies to NUMERIC(20,4); half-even would round these to the evens."""

    def hashed(amount: str) -> str:
        return dedupe.fingerprint(
            account_id=ACCOUNT,
            booked_date=date(2026, 3, 4),
            amount=Decimal(amount),
            description_raw="COFFEE SHOP",
            disambiguator="0",
        )

    assert hashed("310.00005") == hashed("310.0001")
    assert hashed("-310.00005") == hashed("-310.0001")


# --- the audit record ------------------------------------------------------


def test_classification_never_modifies_the_rows_it_reads() -> None:
    rows = [nrow(1, description="Coffee   Shop, Ltd."), nrow(2)]
    before = [(r.description_raw, r.description_clean, r.amount) for r in rows]
    classify(rows, prior=[committed(rows[1])])
    assert [(r.description_raw, r.description_clean, r.amount) for r in rows] == before
