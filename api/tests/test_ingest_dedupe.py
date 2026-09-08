"""M2 Task 4b: dedupe — the transaction fingerprint and the near-duplicate warning.

Pure values, no database and no network: the caller supplies the committed rows to compare
against. The spec's section 4.7 layers 2 and 3 are the contract; layer 1, the document checksum,
belongs to Task 5c. Every trap the spec names is asserted here rather than assumed — above all
that the occurrence index is counted **within this document only**, which is what lets an
overlapping month's rows collide with their committed copies instead of importing twice.
"""

from __future__ import annotations

from datetime import date, timedelta
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
            disambiguator=f"occ:{occurrence}",
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
        disambiguator="occ:0",
    )
    noisy = dedupe.fingerprint(
        account_id=ACCOUNT,
        booked_date=date(2026, 3, 4),
        amount=Decimal("-310.0000"),
        description_raw="  COFFEE,  SHOP.  ",
        disambiguator="occ:0",
    )
    assert quiet == noisy


def test_the_fingerprint_is_stable_and_is_sha256_hex() -> None:
    def hashed() -> str:
        return dedupe.fingerprint(
            account_id=ACCOUNT,
            booked_date=date(2026, 3, 4),
            amount=Decimal("-310.0000"),
            description_raw="COFFEE SHOP",
            disambiguator="occ:0",
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
            disambiguator="occ:0",
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
    """Distinct references separate the rows; the index is reported but not used in the hash."""
    rows = [nrow(1, reference="REF-A"), nrow(2, reference="REF-B")]
    verdicts = classify(rows, maps_reference=True)
    assert verdicts[0].dedupe_hash != verdicts[1].dedupe_hash

    # Both rows would have collided on the index alone, so the difference comes from the
    # reference: dropping it must change both hashes.
    unreferenced = classify(rows, maps_reference=False)
    assert [v.dedupe_hash for v in unreferenced] != [v.dedupe_hash for v in verdicts]


def test_a_repeated_reference_still_separates_distinct_rows() -> None:
    """A reference separates identical-looking transactions only while it is unique.

    Some banks fill the column with a constant. Three distinct coffees would then share one
    fingerprint, stage as clean, and be rejected wholesale by V10's unique index at commit —
    after the owner has reviewed the batch, with nothing left behind to diagnose it.
    """
    rows = [nrow(n, reference="NA") for n in (1, 2, 3)]
    verdicts = classify(rows, maps_reference=True)
    assert [v.status for v in verdicts] == ["new", "new", "new"]
    assert len({v.dedupe_hash for v in verdicts}) == 3

    # Stable across statements: the same rows in the same order reproduce the same hashes, so an
    # overlapping month is still recognised.
    assert [v.dedupe_hash for v in classify(rows, maps_reference=True)] == [
        v.dedupe_hash for v in verdicts
    ]


def test_a_referenced_row_still_consumes_its_occurrence_index() -> None:
    """Otherwise a reference present in one statement and blank in the next shifts every index.

    Each row would then be marked a duplicate of the wrong transaction — and
    `duplicate_of_transaction_id` is a stored fact under I7.
    """
    verdicts = classify([nrow(1, reference="REF-1"), nrow(2), nrow(3)], maps_reference=True)
    assert [v.occurrence_index for v in verdicts] == [0, 1, 2]


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
            disambiguator="occ:0",
        )

    assert hashed("310.00005") == hashed("310.0001")
    assert hashed("-310.00005") == hashed("-310.0001")


# --- the boundaries the verdicts turn on -----------------------------------


def test_the_hash_is_pinned_to_a_known_vector() -> None:
    """A silent change to the hash construction means a ledger-wide re-import.

    Every other test derives the expected hash from this same function, so only a literal pins
    the bytes. If this fails, the construction changed and that is a migration, not a patch.
    """
    assert (
        dedupe.fingerprint(
            account_id=ACCOUNT,
            booked_date=date(2026, 3, 4),
            amount=Decimal("-310.0000"),
            description_raw="Coffee Shop, Ltd.",
            disambiguator="occ:0",
        )
        == "7b1289eca85ff88d157b06d93f26ef7e94b2b77dd3940c3fa4d4830706998bf8"
    )


def test_the_same_row_on_two_accounts_fingerprints_differently() -> None:
    def hashed(account: UUID) -> str:
        return dedupe.fingerprint(
            account_id=account,
            booked_date=date(2026, 3, 4),
            amount=Decimal("-310.0000"),
            description_raw="COFFEE SHOP",
            disambiguator="occ:0",
        )

    assert hashed(ACCOUNT) != hashed(UUID("22222222-2222-4222-8222-222222222222"))


def test_punctuation_becomes_a_boundary_rather_than_closing_the_gap() -> None:
    assert dedupe.normalise_for_hash("A.B") == "A B"
    assert dedupe.normalise_for_hash("A.B") != dedupe.normalise_for_hash("AB")


def test_the_occurrence_key_separates_dates_and_amounts() -> None:
    """Identical descriptions on different days, or for different amounts, both start at 0."""
    dates = classify([nrow(1), nrow(2, booked_date=date(2026, 3, 5))])
    assert [v.occurrence_index for v in dates] == [0, 0]

    amounts = classify([nrow(1), nrow(2, amount="-99.0000")])
    assert [v.occurrence_index for v in amounts] == [0, 0]


def test_the_near_duplicate_window_holds_at_three_days_and_not_at_four() -> None:
    def at(gap: int) -> str:
        # The date is part of the hash, so a shifted candidate can never be an exact duplicate
        # and the verdict turns on the window alone.
        earlier = committed(nrow(1, booked_date=date(2026, 3, 4) - timedelta(days=gap)))
        [verdict] = classify([nrow(1)], prior=[earlier])
        return verdict.status

    assert at(3) == "near_duplicate"
    assert at(4) == "new"


def test_the_similarity_threshold_holds_at_the_boundary() -> None:
    """0.8 is below and 0.857 above; neither is 0.0 or 1.0, so the constant is pinned."""
    below = "STARBUCKS COFFEE"
    above = "STARBUCKS COFFEE LONDON BRIDGE"
    assert dedupe.token_set_ratio("STARBUCKS COFFEE LONDON", below) < 0.85
    assert dedupe.token_set_ratio("STARBUCKS COFFEE LONDON", above) >= 0.85

    def against(other: str) -> str:
        earlier = committed(nrow(1, booked_date=date(2026, 3, 3), description=other))
        row = nrow(1, description="STARBUCKS COFFEE LONDON")
        [verdict] = classify([row], prior=[earlier])
        return verdict.status

    assert against(below) == "new"
    assert against(above) == "near_duplicate"


def test_the_closest_candidate_wins_rather_than_the_first_supplied() -> None:
    # Both candidates sit on other dates, so neither can be an exact duplicate and the verdict
    # turns on the match quality alone. The weaker one is supplied first.
    weaker = committed(
        nrow(1, booked_date=date(2026, 3, 1), description="STARBUCKS COFFEE LONDON BRIDGE"),
        txn_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    closer = committed(
        nrow(1, booked_date=date(2026, 3, 3), description="STARBUCKS COFFEE LONDON"),
        txn_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    )
    [verdict] = classify([nrow(1, description="STARBUCKS COFFEE LONDON")], prior=[weaker, closer])
    assert verdict.status == "near_duplicate"
    assert verdict.near_duplicate_of_id == closer.id


def test_token_set_ratio_handles_empty_descriptions() -> None:
    """A description of only punctuation normalises to nothing."""
    assert dedupe.normalise_for_hash("...") == ""
    assert dedupe.token_set_ratio("", "") == 1.0
    assert dedupe.token_set_ratio("", "COFFEE") == 0.0


def test_a_reference_that_looks_like_an_index_does_not_collide() -> None:
    """The two disambiguators come from different worlds and must not share a namespace.

    A bank reference of "1" and an occurrence index of 1 are unrelated facts. Sharing one
    namespace gave two distinct rows one fingerprint, which V10's unique index rejects at commit
    — or, if one is already committed, marks the other a false duplicate and excludes it.
    """
    verdicts = classify([nrow(1, reference="1"), nrow(2)], maps_reference=True)
    assert verdicts[0].dedupe_hash != verdicts[1].dedupe_hash


def test_a_literal_reference_cannot_collide_with_a_generated_repeat() -> None:
    """ "ABC" seen twice must not produce the hash of a literal reference "ABC#1"."""
    verdicts = classify(
        [nrow(1, reference="ABC"), nrow(2, reference="ABC"), nrow(3, reference="ABC#1")],
        maps_reference=True,
    )
    assert len({v.dedupe_hash for v in verdicts}) == 3


def test_case_only_variants_normalise_identically() -> None:
    """Uppercasing expands some letters into a base plus a combining mark.

    Greek "Μαΐου" is one: a mark introduced after the only NFKC pass would be deleted
    uncomposed, so the lowercase and uppercase forms hashed differently and scored 0.0 against
    each other — an overlapping transaction imported twice.
    """
    lower = "Μαΐου"
    assert dedupe.normalise_for_hash(lower) == dedupe.normalise_for_hash(lower.upper())


# --- the audit record ------------------------------------------------------


def test_classification_never_modifies_the_rows_it_reads() -> None:
    rows = [nrow(1, description="Coffee   Shop, Ltd."), nrow(2)]
    before = [(r.description_raw, r.description_clean, r.amount) for r in rows]
    classify(rows, prior=[committed(rows[1])])
    assert [(r.description_raw, r.description_clean, r.amount) for r in rows] == before
