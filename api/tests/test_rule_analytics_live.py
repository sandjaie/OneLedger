"""Rule analytics and suggestions against a real database (Phase 2.3).

Coverage, per-rule hits and disagreements, and suggestions are all read-only measurement over
what the cascade has already decided — the tests that matter most prove that reading them moves
no ledger row, and that a suggestion's affected/unclassified figures are exactly what
`POST /rules/preview` would report for the same rule body.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, Fixture, _clean, _connect

CLASSIFIABLE = (
    "classified_by",
    "classification_rule_id",
    "classification_confidence",
    "category_id",
    "merchant_id",
    "txn_type",
    "payment_method",
)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Fixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT id FROM category")
        categories: list[UUID] = [row[0] for row in cur.fetchall()]
    try:
        fixture = Fixture(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            cur.execute("DELETE FROM classification_rule")
            cur.execute("DELETE FROM category WHERE NOT (id = ANY(%s))", (categories,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def client() -> TestClient:
    return TestClient(app)


def snapshot(live: Fixture) -> list[tuple[Any, ...]]:
    with live.conn.cursor() as cur:
        cur.execute(f"SELECT id, {', '.join(CLASSIFIABLE)} FROM transaction ORDER BY id")
        return list(cur.fetchall())


def a_category(live: Fixture, slug: str) -> UUID:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO category (household_id, slug, name, kind, bucket)
               VALUES (%s, %s, %s, 'expense', 'need') RETURNING id""",
            (live.household_id, slug, slug.replace("-", " ")),
        )
        row = cur.fetchone()
        assert row is not None
    live.conn.commit()
    return UUID(str(row[0]))


def a_transaction(
    live: Fixture,
    description: str,
    *,
    amount: Decimal = Decimal("-10.0000"),
    booked_date: date = date(2026, 3, 2),
    classified_by: str = "unclassified",
    classification_confidence: Decimal | None = None,
    classification_rule_id: UUID | None = None,
    category_id: UUID | None = None,
    txn_type: str = "expense",
) -> UUID:
    """One committed row, classified however the caller says. Invented figures throughout."""
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, description_clean,
                   amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                   amount_base, amount_secondary, txn_type, source, dedupe_hash,
                   classified_by, classification_confidence, classification_rule_id, category_id)
               VALUES (%s, %s, %s, %s, %s,
                       %s, %s, %s, 1, 1,
                       %s, %s, %s::txn_type, 'manual', %s,
                       %s::classified_by, %s, %s, %s)
               RETURNING id""",
            (
                live.household_id,
                live.account_id,
                booked_date,
                description,
                description,
                amount,
                live.base,
                booked_date,
                amount,
                amount,
                txn_type,
                uuid4().hex + uuid4().hex[:32],
                classified_by,
                classification_confidence,
                classification_rule_id,
                category_id,
            ),
        )
        row = cur.fetchone()
        assert row is not None
    live.conn.commit()
    return UUID(str(row[0]))


def preview_body(
    pattern: str, direction: str, category_id: UUID, txn_type: str
) -> dict[str, object]:
    return {
        "matchDescriptionPattern": pattern,
        "matchPatternKind": "contains",
        "matchDirection": direction,
        "setCategoryId": str(category_id),
        "setTxnType": txn_type,
    }


# ---------------------------------------------------------------------------------------
# Coverage.
# ---------------------------------------------------------------------------------------


def test_coverage_counts_by_status_and_confidence_band_over_the_anchor_window(
    live: Fixture,
) -> None:
    manual_cat = a_category(live, "analytics-manual")
    a_transaction(
        live,
        "MANUAL ROW",
        classified_by="user_manual",
        classification_confidence=Decimal("1.000"),
        category_id=manual_cat,
        booked_date=date(2026, 1, 1),
    )
    a_transaction(
        live,
        "RULE ROW",
        classified_by="user_rule",
        classification_confidence=Decimal("0.980"),
        category_id=manual_cat,
        booked_date=date(2026, 1, 2),
    )
    a_transaction(
        live,
        "UNCLASSIFIED LOW",
        classified_by="unclassified",
        classification_confidence=Decimal("0.000"),
        booked_date=date(2026, 1, 3),
    )
    a_transaction(
        live,
        "UNCLASSIFIED NONE",
        classified_by="unclassified",
        classification_confidence=None,
        booked_date=date(2026, 1, 4),
    )
    # After today is outside the window (`coverage.HISTORY_ANCHOR` through today) exactly as
    # before the anchor would be; the fixture's account opens in 2026, so a future date is the
    # window edge this suite can actually book.
    a_transaction(
        live,
        "OUTSIDE WINDOW",
        classified_by="user_manual",
        classification_confidence=Decimal("1.000"),
        category_id=manual_cat,
        booked_date=date(2030, 1, 1),
    )

    coverage = client().get("/rules/analytics", headers=ORIGIN).json()["coverage"]

    assert coverage["totalRows"] == 4
    assert coverage["byClassifiedBy"]["user_manual"] == {"count": 1, "percent": "25.00"}
    assert coverage["byClassifiedBy"]["user_rule"] == {"count": 1, "percent": "25.00"}
    assert coverage["byClassifiedBy"]["unclassified"] == {"count": 2, "percent": "50.00"}
    assert coverage["byClassifiedBy"]["merchant_alias"] == {"count": 0, "percent": "0.00"}
    assert coverage["confidenceBands"]["above_0.8"] == {"count": 2, "percent": "50.00"}
    assert coverage["confidenceBands"]["below_0.5"] == {"count": 1, "percent": "25.00"}
    assert coverage["confidenceBands"]["none"] == {"count": 1, "percent": "25.00"}
    assert coverage["confidenceBands"]["0.5_to_0.8"] == {"count": 0, "percent": "0.00"}
    assert coverage["reportableUncategorized"] == {"count": 2, "percent": "50.00"}


def test_coverage_percent_is_absent_when_nothing_is_in_the_window(live: Fixture) -> None:
    coverage = client().get("/rules/analytics", headers=ORIGIN).json()["coverage"]

    assert coverage["totalRows"] == 0
    assert coverage["byClassifiedBy"]["unclassified"] == {"count": 0, "percent": None}
    assert coverage["reportableUncategorized"] == {"count": 0, "percent": None}


# ---------------------------------------------------------------------------------------
# Per-rule hits, last hit date and disagreements.
# ---------------------------------------------------------------------------------------


def test_rule_hits_last_hit_date_and_disagreements(live: Fixture) -> None:
    agrees_cat = a_category(live, "hits-agrees")
    disagrees_cat = a_category(live, "hits-disagrees")
    created = (
        client()
        .post(
            "/rules",
            json={
                "matchDescriptionPattern": "GROCER",
                "matchPatternKind": "contains",
                "setCategoryId": str(agrees_cat),
                "priority": 100,
            },
            headers=ORIGIN,
        )
        .json()
    )
    rule_id = UUID(created["id"])

    a_transaction(
        live,
        "GROCER MARKET",
        classified_by="user_rule",
        classification_rule_id=rule_id,
        category_id=agrees_cat,
        classification_confidence=Decimal("0.980"),
        booked_date=date(2026, 2, 1),
    )
    a_transaction(
        live,
        "GROCER MARKET TOO",
        classified_by="user_rule",
        classification_rule_id=rule_id,
        category_id=agrees_cat,
        classification_confidence=Decimal("0.980"),
        booked_date=date(2026, 2, 5),
    )
    # The rule would decide this row too (its pattern matches), but its stored answer is a
    # different category and a different provenance — the disagreement the row set is judged on.
    a_transaction(
        live,
        "GROCER SHOP",
        classified_by="user_manual",
        classification_rule_id=None,
        category_id=disagrees_cat,
        classification_confidence=Decimal("1.000"),
        booked_date=date(2026, 2, 3),
    )
    # Matches nothing the rule's pattern covers, and must not be counted either way.
    a_transaction(
        live, "AIRLINE TICKET", classified_by="unclassified", booked_date=date(2026, 2, 4)
    )

    analytics = client().get("/rules/analytics", headers=ORIGIN).json()
    hit = next(r for r in analytics["rules"] if r["ruleId"] == str(rule_id))

    assert hit["hits"] == 2
    assert hit["lastHitDate"] == "2026-02-05"
    assert hit["disagreements"] == 1
    assert len(hit["disagreementSample"]) == 1
    assert hit["disagreementSample"][0]["description"] == "GROCER SHOP"
    assert hit["disagreementSample"][0]["bookedDate"] == "2026-02-03"


def test_disagreement_is_counted_only_on_the_earlier_priority_rule(live: Fixture) -> None:
    """Two active rules both match the same row; `_first_matching` picks exactly one winner per
    row within a priority group, so a disagreement can only ever land on the rule the cascade
    would actually have chosen — never on the rule priority order never reaches."""
    earlier_cat = a_category(live, "overlap-earlier")
    later_cat = a_category(live, "overlap-later")
    stored_cat = a_category(live, "overlap-stored")
    earlier = (
        client()
        .post(
            "/rules",
            json={
                "matchDescriptionPattern": "GROCER",
                "matchPatternKind": "contains",
                "setCategoryId": str(earlier_cat),
                "priority": 10,
            },
            headers=ORIGIN,
        )
        .json()
    )
    later = (
        client()
        .post(
            "/rules",
            json={
                "matchDescriptionPattern": "GROCER",
                "matchPatternKind": "contains",
                "setCategoryId": str(later_cat),
                "priority": 20,
            },
            headers=ORIGIN,
        )
        .json()
    )
    # Stored by hand, under neither rule's own category: both rules would dispute this row if
    # priority order did not decide a single winner.
    a_transaction(
        live,
        "GROCER MARKET",
        classified_by="user_manual",
        category_id=stored_cat,
        classification_confidence=Decimal("1.000"),
        booked_date=date(2026, 9, 1),
    )

    analytics = client().get("/rules/analytics", headers=ORIGIN).json()
    by_id = {rule["ruleId"]: rule for rule in analytics["rules"]}

    assert by_id[earlier["id"]]["disagreements"] == 1
    assert by_id[later["id"]]["disagreements"] == 0


def test_top_unclassified_descriptions_are_grouped_counted_and_redacted(live: Fixture) -> None:
    a_transaction(live, "REPEATED SHOP", amount=Decimal("-10.0000"), booked_date=date(2026, 3, 1))
    # Case and spacing differ, but the normalised identity is the same narration.
    a_transaction(
        live, "  repeated   shop  ", amount=Decimal("-20.0000"), booked_date=date(2026, 3, 2)
    )
    identifier = "1234-5678-9012-3456"
    a_transaction(
        live, f"ONE-OFF SHOP {identifier}", amount=Decimal("-5.0000"), booked_date=date(2026, 3, 3)
    )

    top = client().get("/rules/analytics", headers=ORIGIN).json()["topUnclassified"]

    assert identifier not in str(top)
    repeated = next(row for row in top if "REPEATED SHOP" in row["description"].upper())
    assert repeated["count"] == 2
    assert Decimal(repeated["totalAbsAmountBase"]) == Decimal("30.0000")


# ---------------------------------------------------------------------------------------
# Suggestions.
# ---------------------------------------------------------------------------------------


def test_suggestions_need_three_corrections_and_never_duplicate_an_active_rule(
    live: Fixture,
) -> None:
    coffee_cat = a_category(live, "sugg-coffee")
    tea_cat = a_category(live, "sugg-tea")
    bakery_cat = a_category(live, "sugg-bakery")

    for day in (1, 2, 3):
        a_transaction(
            live,
            "COFFEEHOUSE",
            classified_by="user_manual",
            category_id=coffee_cat,
            classification_confidence=Decimal("1.000"),
            booked_date=date(2026, 5, day),
        )
    # An unclassified row sharing the pattern: proves `affected`/`unclassified` see it too.
    a_transaction(live, "COFFEEHOUSE", classified_by="unclassified", booked_date=date(2026, 5, 10))

    # Two corrections is not enough evidence.
    for day in (1, 2):
        a_transaction(
            live,
            "TEAHOUSE",
            classified_by="user_manual",
            category_id=tea_cat,
            classification_confidence=Decimal("1.000"),
            booked_date=date(2026, 5, day),
        )

    # An active rule already produces this exact outcome for this exact pattern.
    client().post(
        "/rules",
        json={
            "matchDescriptionPattern": "BAKERY",
            "matchPatternKind": "contains",
            "matchDirection": "debit",
            "setCategoryId": str(bakery_cat),
            "setTxnType": "expense",
        },
        headers=ORIGIN,
    )
    for day in (1, 2, 3):
        a_transaction(
            live,
            "BAKERY",
            classified_by="user_manual",
            category_id=bakery_cat,
            classification_confidence=Decimal("1.000"),
            booked_date=date(2026, 5, day),
        )

    suggestions = client().get("/rules/suggestions", headers=ORIGIN).json()["suggestions"]
    patterns = {suggestion["pattern"] for suggestion in suggestions}

    assert "COFFEEHOUSE" in patterns
    assert "TEAHOUSE" not in patterns
    assert "BAKERY" not in patterns

    coffee = next(s for s in suggestions if s["pattern"] == "COFFEEHOUSE")
    assert coffee["evidenceRows"] == 3
    assert coffee["direction"] == "debit"
    assert coffee["categoryId"] == str(coffee_cat)
    assert coffee["txnType"] == "expense"

    previewed = (
        client()
        .post(
            "/rules/preview",
            json=preview_body("COFFEEHOUSE", "debit", coffee_cat, "expense"),
            headers=ORIGIN,
        )
        .json()
    )
    assert coffee["affected"] == previewed["affected"] == 4
    assert coffee["unclassified"] == previewed["unclassified"] == 1


def test_suggestions_stay_hidden_for_a_category_only_rule_on_the_same_pattern(
    live: Fixture,
) -> None:
    """`classify-prompt.tsx`'s own `ruleBody()` never sets a transaction type — only a category
    — so a rule silent on type must still read as already covering a suggestion's outcome, or
    every rule the classify prompt creates would keep re-suggesting itself forever."""
    cat = a_category(live, "sugg-category-only")
    client().post(
        "/rules",
        json={
            "matchDescriptionPattern": "BAKERY",
            "matchPatternKind": "contains",
            "matchDirection": "debit",
            "setCategoryId": str(cat),
            # No setTxnType: exactly the shape classify-prompt.tsx's ruleBody() creates.
        },
        headers=ORIGIN,
    )
    for day in (1, 2, 3):
        a_transaction(
            live,
            "BAKERY",
            classified_by="user_manual",
            category_id=cat,
            classification_confidence=Decimal("1.000"),
            booked_date=date(2026, 8, day),
        )

    suggestions = client().get("/rules/suggestions", headers=ORIGIN).json()["suggestions"]

    assert "BAKERY" not in {suggestion["pattern"] for suggestion in suggestions}


def test_suggestions_are_ordered_by_evidence_rows_and_capped(live: Fixture) -> None:
    small_cat = a_category(live, "sugg-small")
    big_cat = a_category(live, "sugg-big")
    for day in (1, 2, 3):
        a_transaction(
            live,
            "SMALLPATTERN",
            classified_by="user_manual",
            category_id=small_cat,
            classification_confidence=Decimal("1.000"),
            booked_date=date(2026, 6, day),
        )
    for day in (1, 2, 3, 4, 5):
        a_transaction(
            live,
            "BIGPATTERN",
            classified_by="user_manual",
            category_id=big_cat,
            classification_confidence=Decimal("1.000"),
            booked_date=date(2026, 6, day),
        )

    suggestions = client().get("/rules/suggestions", headers=ORIGIN).json()["suggestions"]

    assert [s["pattern"] for s in suggestions[:2]] == ["BIGPATTERN", "SMALLPATTERN"]


# ---------------------------------------------------------------------------------------
# Isolation and side effects.
# ---------------------------------------------------------------------------------------


def test_analytics_and_suggestions_are_isolated_to_the_household(live: Fixture) -> None:
    a_transaction(live, "MINE ONLY", booked_date=date(2026, 4, 1))
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('analytics elsewhere', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        other_household = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO person (household_id, display_name, is_self)
               VALUES (%s, 'Other owner', true) RETURNING id""",
            (other_household,),
        )
        other_person = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                    country, opening_balance, opening_balance_date)
               VALUES (%s, %s, 'foreign analytics account', 'savings', %s, 'ZZ', 0,
                       DATE '2026-01-01')
               RETURNING id""",
            (other_household, other_person, live.base),
        )
        other_account = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        amount, currency, fx_rate_date, fx_rate_to_base,
                                        fx_rate_to_secondary, amount_base, amount_secondary,
                                        txn_type, source, dedupe_hash, classified_by,
                                        classification_confidence)
               VALUES (%s, %s, DATE '2026-04-01', 'FOREIGN ROW', -5.0000, %s, DATE '2026-04-01',
                       1, 1, -5.0000, -5.0000, 'expense', 'manual', %s, 'unclassified', 0)""",
            (other_household, other_account, live.base, uuid4().hex * 2),
        )
        cur.execute(
            """INSERT INTO classification_rule (household_id, match_description_pattern,
                                                set_txn_type, origin)
               VALUES (%s, 'FOREIGN', 'expense', 'user_created')""",
            (other_household,),
        )
    live.conn.commit()

    try:
        analytics = client().get("/rules/analytics", headers=ORIGIN).json()
        suggestions = client().get("/rules/suggestions", headers=ORIGIN).json()

        assert analytics["coverage"]["totalRows"] == 1
        assert analytics["rules"] == []
        assert suggestions["suggestions"] == []
    finally:
        # `_clean` never truncates `account`, so this suite cleans up its own foreign household
        # rather than leaving an account behind that blocks the shared fixture's own teardown.
        with live.conn.cursor() as cur:
            cur.execute("DELETE FROM transaction WHERE household_id = %s", (other_household,))
            cur.execute(
                "DELETE FROM classification_rule WHERE household_id = %s", (other_household,)
            )
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE household_id = %s", (other_household,))
            cur.execute("DELETE FROM person WHERE household_id = %s", (other_household,))
            cur.execute("DELETE FROM household WHERE id = %s", (other_household,))
        live.conn.commit()


def test_analytics_suggestions_and_rule_creation_move_no_transaction_row(live: Fixture) -> None:
    cat = a_category(live, "no-move-cat")
    for day in (1, 2, 3):
        a_transaction(
            live,
            "TIDYSHOP",
            classified_by="user_manual",
            category_id=cat,
            classification_confidence=Decimal("1.000"),
            booked_date=date(2026, 7, day),
        )
    before = snapshot(live)

    client().get("/rules/analytics", headers=ORIGIN)
    client().get("/rules/suggestions", headers=ORIGIN)
    client().post(
        "/rules",
        json={
            "matchDescriptionPattern": "TIDYSHOP",
            "matchPatternKind": "contains",
            "setCategoryId": str(cat),
            "setTxnType": "expense",
        },
        headers=ORIGIN,
    )

    assert snapshot(live) == before


def test_analytics_and_suggestions_are_refused_without_a_session() -> None:
    app.dependency_overrides.clear()
    anonymous = TestClient(app)

    assert anonymous.get("/rules/analytics").status_code == 401
    assert anonymous.get("/rules/suggestions").status_code == 401
