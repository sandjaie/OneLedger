"""Grouped classification-review behavior without a database.

The endpoint is read-only. These tests prove which rows become questions and that grouping is
based on stable narration/account/direction identity rather than coincidental amount equality.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.ingest.classify import Alias, Knowledge
from oneledger.main import app
from oneledger.routes.classification_review import ReviewCandidate, build_review_groups

HOUSEHOLD_ID = UUID("11111111-1111-1111-1111-111111111111")
ACCOUNT_ID = UUID("22222222-2222-2222-2222-222222222222")
CATEGORY_ID = UUID("33333333-3333-3333-3333-333333333333")
MERCHANT_ID = UUID("44444444-4444-4444-4444-444444444444")
COUNTERPARTY_ID = UUID("55555555-5555-5555-5555-555555555555")
KNOWLEDGE_VERSION_ID = UUID("66666666-6666-6666-6666-666666666666")


def candidate(**changes: object) -> ReviewCandidate:
    values: dict[str, object] = {
        "transaction_id": uuid4(),
        "booked_date": date(2026, 8, 10),
        "account_id": ACCOUNT_ID,
        "account_name": "Synthetic current account",
        "description": "SYNTHETIC CORNER SHOP",
        "amount": Decimal("-12.0000"),
        "currency": "XTS",
        "reference_raw": None,
        "country": None,
        "txn_type": "expense",
        "category_id": None,
        "category_name": None,
        "merchant_id": None,
        "payment_method": None,
        "is_excluded": False,
        "classified_by": "unclassified",
        "classification_confidence": Decimal("0.000"),
        "transfer_id": None,
        "is_split_parent": False,
    }
    values.update(changes)
    return ReviewCandidate(**values)  # type: ignore[arg-type]


def test_same_stable_question_groups_different_amounts_without_returning_money() -> None:
    first = candidate(amount=Decimal("-12.0000"))
    second = candidate(amount=Decimal("-91.0000"), booked_date=date(2026, 7, 9))

    groups = build_review_groups(HOUSEHOLD_ID, [first, second], Knowledge(), category_names={})

    assert len(groups) == 1
    group = groups[0]
    assert group.reason == "unclassified"
    assert group.occurrences == 2
    assert set(group.transactionIds) == {first.transaction_id, second.transaction_id}
    serialized = group.model_dump_json()
    assert "amount" not in serialized.lower()
    assert "-12.0000" not in serialized
    assert "-91.0000" not in serialized


def test_recognised_purchase_needs_no_review_click() -> None:
    recognised = Knowledge(
        aliases=[
            Alias(
                merchant_id=MERCHANT_ID,
                pattern="SYNTHETIC CORNER SHOP",
                pattern_kind="exact",
                category_id=CATEGORY_ID,
                counterparty_id=COUNTERPARTY_ID,
                knowledge_version_id=KNOWLEDGE_VERSION_ID,
                role="purchase",
                direction="debit",
                txn_type="expense",
            )
        ]
    )

    assert (
        build_review_groups(
            HOUSEHOLD_ID,
            [
                candidate(
                    category_id=CATEGORY_ID,
                    merchant_id=MERCHANT_ID,
                    classified_by="merchant_alias",
                    classification_confidence=Decimal("0.950"),
                )
            ],
            recognised,
            category_names={CATEGORY_ID: "Groceries"},
        )
        == []
    )


def test_recognised_but_unapplied_knowledge_retains_guarded_repair_scope() -> None:
    recognised = Knowledge(
        aliases=[
            Alias(
                merchant_id=MERCHANT_ID,
                pattern="SYNTHETIC CORNER SHOP",
                pattern_kind="exact",
                category_id=CATEGORY_ID,
                counterparty_id=COUNTERPARTY_ID,
                knowledge_version_id=KNOWLEDGE_VERSION_ID,
                role="purchase",
                direction="debit",
                txn_type="expense",
            )
        ]
    )

    group = build_review_groups(
        HOUSEHOLD_ID,
        [candidate()],
        recognised,
        category_names={CATEGORY_ID: "Groceries"},
    )[0]

    assert group.reason == "recognized_pending"
    assert group.knowledgeVersionId == KNOWLEDGE_VERSION_ID
    assert group.transactionIds
    assert group.rememberable is False


def test_changed_high_confidence_knowledge_stays_actionable_until_applied() -> None:
    old_category = uuid4()
    revised = Knowledge(
        aliases=[
            Alias(
                merchant_id=MERCHANT_ID,
                pattern="SYNTHETIC CORNER SHOP",
                pattern_kind="exact",
                category_id=CATEGORY_ID,
                counterparty_id=COUNTERPARTY_ID,
                knowledge_version_id=KNOWLEDGE_VERSION_ID,
                role="purchase",
                direction="debit",
                txn_type="expense",
            )
        ]
    )

    group = build_review_groups(
        HOUSEHOLD_ID,
        [
            candidate(
                category_id=old_category,
                merchant_id=MERCHANT_ID,
                classified_by="merchant_alias",
                classification_confidence=Decimal("0.950"),
            )
        ],
        revised,
        category_names={CATEGORY_ID: "Groceries", old_category: "Shopping"},
    )[0]

    assert group.reason == "recognized_pending"
    assert group.currentCategoryId == old_category
    assert group.proposedCategoryId == CATEGORY_ID


def test_broad_category_fallback_stays_a_grouped_identity_question() -> None:
    broad = Knowledge(
        aliases=[
            Alias(
                merchant_id=None,
                pattern="SYNTHETIC CORNER SHOP",
                pattern_kind="exact",
                category_id=CATEGORY_ID,
                counterparty_id=COUNTERPARTY_ID,
                knowledge_version_id=KNOWLEDGE_VERSION_ID,
                role="unresolved",
                direction="debit",
                txn_type="expense",
            )
        ]
    )

    group = build_review_groups(
        HOUSEHOLD_ID,
        [candidate()],
        broad,
        category_names={CATEGORY_ID: "Household"},
    )[0]

    assert group.reason == "identity_unresolved"
    assert group.proposedCategoryId == CATEGORY_ID
    assert group.proposedCategoryName == "Household"
    assert group.knowledgeVersionId == KNOWLEDGE_VERSION_ID
    assert group.rememberable is True


def test_equal_strength_conflicting_knowledge_abstains_as_one_question() -> None:
    aliases = [
        Alias(
            merchant_id=merchant,
            pattern="SYNTHETIC CORNER SHOP",
            pattern_kind="exact",
            priority=10,
            category_id=category,
            counterparty_id=counterparty,
            knowledge_version_id=uuid4(),
            role="purchase",
            direction="debit",
            txn_type="expense",
        )
        for merchant, category, counterparty in [
            (MERCHANT_ID, CATEGORY_ID, COUNTERPARTY_ID),
            (uuid4(), uuid4(), uuid4()),
        ]
    ]

    group = build_review_groups(
        HOUSEHOLD_ID, [candidate()], Knowledge(aliases=aliases), category_names={}
    )[0]

    assert group.reason == "conflicting_knowledge"
    assert group.rememberable is False
    assert "conflict" in group.explanation.lower()


def test_report_excluding_role_is_warned_even_when_it_is_not_flagged_excluded() -> None:
    group = build_review_groups(
        HOUSEHOLD_ID,
        [candidate(txn_type="investment_buy", classified_by="seed_rule")],
        Knowledge(),
        category_names={},
    )[0]

    assert group.reason == "excluded_role"
    assert "spending" in group.explanation.lower()


def test_newly_proposed_report_excluding_role_is_warned_before_application() -> None:
    excluding = Knowledge(
        aliases=[
            Alias(
                merchant_id=None,
                pattern="SYNTHETIC CORNER SHOP",
                pattern_kind="exact",
                counterparty_id=COUNTERPARTY_ID,
                knowledge_version_id=KNOWLEDGE_VERSION_ID,
                role="institution",
                direction="debit",
                txn_type="loan_repayment",
            )
        ]
    )

    group = build_review_groups(HOUSEHOLD_ID, [candidate()], excluding, category_names={})[0]

    assert group.reason == "excluded_role"
    assert group.currentTxnType == "expense"
    assert group.proposedTxnType == "loan_repayment"


def test_manual_overrides_linked_legs_and_split_parents_are_not_questions() -> None:
    rows = [
        candidate(classified_by="user_manual"),
        candidate(transfer_id=uuid4()),
        candidate(is_split_parent=True),
    ]

    assert build_review_groups(HOUSEHOLD_ID, rows, Knowledge(), category_names={}) == []


def test_group_identity_separates_account_direction_and_question() -> None:
    rows = [
        candidate(),
        candidate(account_id=uuid4()),
        candidate(amount=Decimal("12.0000")),
        candidate(txn_type="investment_buy", classified_by="seed_rule"),
    ]

    groups = build_review_groups(HOUSEHOLD_ID, rows, Knowledge(), category_names={})

    assert len(groups) == 4
    assert len({group.groupKey for group in groups}) == 4


def test_classification_review_requires_an_owner_session() -> None:
    app.dependency_overrides.clear()

    response = TestClient(app).get("/classification-review/groups")

    assert response.status_code == 401


def test_classification_review_bounds_page_size_before_opening_database() -> None:
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(),
        household_id=HOUSEHOLD_ID,
        session_id=uuid4(),
        email="owner@example.invalid",
    )
    try:
        response = TestClient(app).get("/classification-review/groups", params={"limit": 101})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
