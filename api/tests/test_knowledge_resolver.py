"""Private knowledge resolution is deterministic, scoped and willing to abstain."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from oneledger.ingest.classify import Alias, Candidate, Knowledge, Prior, classify_row

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
MERCHANT = UUID("22222222-2222-2222-2222-222222222222")
OTHER_MERCHANT = UUID("33333333-3333-3333-3333-333333333333")
CATEGORY = UUID("44444444-4444-4444-4444-444444444444")
OTHER_CATEGORY = UUID("55555555-5555-5555-5555-555555555555")
COUNTERPARTY = UUID("66666666-6666-6666-6666-666666666666")
VERSION = UUID("77777777-7777-7777-7777-777777777777")


def candidate(description: str, amount: str = "-12.5000") -> Candidate:
    return Candidate(
        account_id=ACCOUNT,
        description_clean=description,
        amount=Decimal(amount),
        currency="XTS",
    )


def alias(pattern: str, **changes: object) -> Alias:
    values: dict[str, object] = {
        "merchant_id": MERCHANT,
        "pattern": pattern,
        "priority": 100,
        "category_id": CATEGORY,
        "counterparty_id": COUNTERPARTY,
        "knowledge_version_id": VERSION,
        "role": "purchase",
    }
    values.update(changes)
    return Alias(**values)  # type: ignore[arg-type]


def test_specific_service_outweighs_its_parent_brand() -> None:
    parent = alias("SYNTHETIC PROVIDER", category_id=OTHER_CATEGORY)
    service = alias("SYNTHETIC PROVIDER QUICK MART", category_id=CATEGORY)

    decided = classify_row(
        candidate("SYNTHETIC PROVIDER QUICK MART ORDER"),
        Knowledge(aliases=[parent, service]),
    )

    assert decided.category_id == CATEGORY
    assert decided.knowledge_version_id == VERSION
    assert decided.counterparty_role == "purchase"


def test_direction_separates_a_purchase_from_its_refund() -> None:
    purchase = alias("SYNTHETIC SHOP", direction="debit", txn_type="expense")
    refund = alias(
        "SYNTHETIC SHOP",
        direction="credit",
        txn_type="refund",
        category_id=OTHER_CATEGORY,
    )

    outgoing = classify_row(
        candidate("SYNTHETIC SHOP ORDER", "-12.5000"), Knowledge(aliases=[refund, purchase])
    )
    incoming = classify_row(
        candidate("SYNTHETIC SHOP REFUND", "12.5000"), Knowledge(aliases=[purchase, refund])
    )

    assert (outgoing.txn_type, outgoing.category_id) == ("expense", CATEGORY)
    assert (incoming.txn_type, incoming.category_id) == ("refund", OTHER_CATEGORY)


def test_an_account_scoped_alias_does_not_escape_its_account() -> None:
    scoped = alias("SYNTHETIC", account_id=UUID(int=99))

    decided = classify_row(candidate("SYNTHETIC ORDER"), Knowledge(aliases=[scoped]))

    assert decided.classified_by == "unclassified"
    assert decided.unresolved_reason is None


def test_equal_strength_conflicting_aliases_abstain_instead_of_using_uuid_order() -> None:
    first = alias("混合MERCHANT", merchant_id=MERCHANT, category_id=CATEGORY)
    second = alias(
        "混合MERCHANT",
        merchant_id=OTHER_MERCHANT,
        category_id=OTHER_CATEGORY,
        counterparty_id=UUID(int=88),
        knowledge_version_id=UUID(int=89),
    )

    forwards = classify_row(candidate("XX混合MERCHANTYY"), Knowledge(aliases=[first, second]))
    backwards = classify_row(candidate("XX混合MERCHANTYY"), Knowledge(aliases=[second, first]))

    assert forwards == backwards
    assert forwards.classified_by == "unclassified"
    assert forwards.unresolved_reason == "conflicting highest-priority knowledge"


def test_duplicate_evidence_for_the_same_outcome_is_not_a_conflict() -> None:
    first = alias("SYNTHETIC SHOP")
    second = alias("SYNTHETIC SHOP", knowledge_version_id=UUID(int=90))

    decided = classify_row(candidate("SYNTHETIC SHOP"), Knowledge(aliases=[second, first]))

    assert decided.classified_by == "merchant_alias"
    assert (decided.merchant_id, decided.category_id) == (MERCHANT, CATEGORY)


def test_untrusted_or_report_excluding_priors_do_not_amplify() -> None:
    uncertain = Prior(
        account_id=ACCOUNT,
        description_clean="SYNTHETIC FUNDING",
        txn_type="expense",
        trusted=False,
    )
    exclusion = Prior(
        account_id=ACCOUNT,
        description_clean="SYNTHETIC FUNDING",
        txn_type="investment_fee",
        trusted=True,
    )

    for prior in (uncertain, exclusion):
        decided = classify_row(candidate("SYNTHETIC FUNDING"), Knowledge(priors=[prior]))
        assert decided.classified_by == "unclassified"
        assert decided.txn_type is None


def test_a_prior_from_the_opposite_direction_is_not_evidence() -> None:
    decided = classify_row(
        candidate("SAME NARRATION", "9.0000"),
        Knowledge(
            priors=[
                Prior(
                    account_id=ACCOUNT,
                    description_clean="SAME NARRATION",
                    category_id=CATEGORY,
                    direction="debit",
                )
            ]
        ),
    )

    assert decided.classified_by == "unclassified"


def test_conflicting_exact_priors_abstain_independent_of_order() -> None:
    first = Prior(
        account_id=ACCOUNT,
        description_clean="SAME NARRATION",
        category_id=CATEGORY,
        direction="debit",
    )
    second = Prior(
        account_id=ACCOUNT,
        description_clean="SAME NARRATION",
        category_id=OTHER_CATEGORY,
        direction="debit",
    )

    answers = [
        classify_row(candidate("SAME NARRATION"), Knowledge(priors=priors))
        for priors in ([first, second], [second, first])
    ]

    assert [answer.classified_by for answer in answers] == ["unclassified", "unclassified"]
    assert {answer.unresolved_reason for answer in answers} == {"conflicting prior evidence"}
