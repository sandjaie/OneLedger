"""The rule routes against a real database.

The one that matters most is `test_creating_a_rule_moves_nothing`: rules never silently change
past data, and the way to prove it is a ledger that already holds rows the rule matches, and a
before/after over every column the cascade could touch.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

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
    try:
        fixture = Fixture(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            # The ledger first: a rule a transaction names cannot be deleted while the
            # transaction is there, which is exactly what this suite proves.
            _clean(cur)
            cur.execute("DELETE FROM classification_rule")
            # Then every household this suite invented. `db/tests/0006` asserts the seed
            # creates at most one, and a test that leaves a second behind fails the next
            # push rather than its own run — which is how this was found.
            cur.execute(
                "DELETE FROM category WHERE household_id IS NOT NULL"
                " AND NOT (household_id = ANY(%s))",
                (seeded,),
            )
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def client() -> TestClient:
    return TestClient(app)


def rule_body(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "matchDescriptionPattern": "GROCER",
        "setTxnType": "expense",
    }
    values.update(changes)
    return values


def a_transaction(live: Fixture, description: str, **columns: object) -> UUID:
    """One committed row, classified however the caller says. Invented figures throughout."""
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        description_clean, amount, currency, fx_rate_date,
                                        fx_rate_to_base, fx_rate_to_secondary, amount_base,
                                        amount_secondary, txn_type, source, dedupe_hash,
                                        classified_by, classification_confidence)
               VALUES (%s, %s, DATE '2026-03-02', %s, %s, -120.0000, %s, DATE '2026-03-02',
                       1, 1, -120.0000, -120.0000, 'expense', 'manual', %s, %s, %s)
               RETURNING id""",
            (
                live.household_id,
                live.account_id,
                description,
                description,
                live.base,
                uuid4().hex + uuid4().hex[:32],
                columns.get("classified_by", "unclassified"),
                columns.get("classification_confidence", 0),
            ),
        )
        row = cur.fetchone()
        assert row is not None
    live.conn.commit()
    return UUID(str(row[0]))


def snapshot(live: Fixture) -> list[tuple[Any, ...]]:
    with live.conn.cursor() as cur:
        cur.execute(
            f"SELECT id, {', '.join(CLASSIFIABLE)} FROM transaction ORDER BY id"  # noqa: S608
        )
        return list(cur.fetchall())


def test_creating_a_rule_moves_nothing(live: Fixture) -> None:
    """The invariant this slice is judged on, asserted against a ledger the rule matches."""
    a_transaction(live, "GROCER MARKET")
    a_transaction(live, "GROCER MARKET SECOND")
    before = snapshot(live)

    created = client().post("/rules", json=rule_body(setTxnType="income"), headers=ORIGIN)

    assert created.status_code == 201
    assert snapshot(live) == before


def test_editing_a_rule_moves_nothing(live: Fixture) -> None:
    a_transaction(live, "GROCER MARKET")
    created = client().post("/rules", json=rule_body(), headers=ORIGIN).json()
    before = snapshot(live)

    edited = client().patch(
        f"/rules/{created['id']}",
        json=rule_body(matchDescriptionPattern="MARKET", setTxnType="income", priority=5),
        headers=ORIGIN,
    )

    assert edited.status_code == 200
    assert edited.json()["priority"] == 5
    assert snapshot(live) == before


def test_rules_list_in_evaluation_order(live: Fixture) -> None:
    for priority in (300, 10, 100):
        client().post("/rules", json=rule_body(priority=priority), headers=ORIGIN)

    listed = client().get("/rules").json()

    assert [rule["priority"] for rule in listed] == [10, 100, 300]
    assert {rule["origin"] for rule in listed} == {"user_created"}


def test_a_created_rule_reads_back_exactly(live: Fixture) -> None:
    body = rule_body(
        name="Groceries",
        priority=42,
        matchAmountMin="10.0000",
        matchAmountMax="500.0000",
        matchDirection="debit",
        setTags=["household"],
    )

    created = client().post("/rules", json=body, headers=ORIGIN).json()

    assert created["name"] == "Groceries"
    assert (created["matchAmountMin"], created["matchAmountMax"]) == ("10.0000", "500.0000")
    assert created["matchDirection"] == "debit"
    assert created["setTags"] == ["household"]
    # The counters start where V19 says and are nobody's to set through this route.
    assert (created["timesApplied"], created["lastAppliedAt"]) == (0, None)


def test_another_households_category_is_refused(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('elsewhere', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        other = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO category (household_id, name, slug, kind, bucket)
               VALUES (%s, 'Theirs', 'rules-live-theirs', 'expense', 'need') RETURNING id""",
            (other,),
        )
        foreign_category = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()

    refused = client().post(
        "/rules", json=rule_body(setCategoryId=str(foreign_category)), headers=ORIGIN
    )

    assert refused.status_code == 422
    assert "not available to this household" in refused.json()["detail"]


def test_another_households_rule_is_not_found(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('elsewhere too', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        other = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO classification_rule (household_id, match_description_pattern,
                                                set_txn_type, origin)
               VALUES (%s, 'THEIRS', 'expense', 'user_created') RETURNING id""",
            (other,),
        )
        foreign_rule = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()

    assert client().get("/rules").json() == []
    assert (
        client().patch(f"/rules/{foreign_rule}", json=rule_body(), headers=ORIGIN).status_code
        == 404
    )
    assert client().delete(f"/rules/{foreign_rule}", headers=ORIGIN).status_code == 404

    # And it is still there: a 404 that deleted the row would be worse than one that lied.
    with live.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM classification_rule WHERE id = %s", (foreign_rule,))
        assert cur.fetchone()[0] == 1  # type: ignore[index]


def test_a_rule_no_row_names_can_be_deleted(live: Fixture) -> None:
    created = client().post("/rules", json=rule_body(), headers=ORIGIN).json()

    assert client().delete(f"/rules/{created['id']}", headers=ORIGIN).status_code == 204
    assert client().get("/rules").json() == []


def test_a_rule_a_row_names_is_refused_and_says_to_deactivate(live: Fixture) -> None:
    """V19's RESTRICT, surfaced as a sentence. The row's provenance outlives the rule (I6)."""
    created = client().post("/rules", json=rule_body(), headers=ORIGIN).json()
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        amount, currency, fx_rate_date, fx_rate_to_base,
                                        fx_rate_to_secondary, amount_base, amount_secondary,
                                        txn_type, source, dedupe_hash, classified_by,
                                        classification_rule_id, classification_confidence)
               VALUES (%s, %s, DATE '2026-03-02', 'GROCER MARKET', -10.0000, %s,
                       DATE '2026-03-02', 1, 1, -10.0000, -10.0000, 'expense', 'manual', %s,
                       'user_rule', %s, 0.980)""",
            (live.household_id, live.account_id, live.base, uuid4().hex * 2, created["id"]),
        )
    live.conn.commit()

    refused = client().delete(f"/rules/{created['id']}", headers=ORIGIN)

    assert refused.status_code == 409
    assert "deactivate it instead" in refused.json()["detail"]

    # Deactivating is the supported path, and it leaves the provenance intact.
    deactivated = client().patch(
        f"/rules/{created['id']}", json=rule_body(isActive=False), headers=ORIGIN
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["isActive"] is False


def test_every_route_is_refused_without_a_session() -> None:
    """`require_owner` is the boundary; the override the fixture installs is not in place."""
    app.dependency_overrides.clear()
    anonymous = TestClient(app)
    rule_id = uuid4()

    assert anonymous.get("/rules").status_code == 401
    assert anonymous.post("/rules", json=rule_body(), headers=ORIGIN).status_code == 401
    assert anonymous.patch(f"/rules/{rule_id}", json=rule_body(), headers=ORIGIN).status_code == 401
    assert anonymous.delete(f"/rules/{rule_id}", headers=ORIGIN).status_code == 401


# ---------------------------------------------------------------------------------------
# Preview, and the backfill. The rule that governs both: a row that already has an answer
# keeps it, whatever any rule says.
# ---------------------------------------------------------------------------------------


def test_preview_counts_what_a_rule_would_match_and_writes_nothing(live: Fixture) -> None:
    a_transaction(live, "GROCER MARKET")
    a_transaction(live, "GROCER MARKET TOO")
    a_transaction(live, "AIRLINE TICKET")
    before = snapshot(live)

    previewed = client().post("/rules/preview", json=rule_body(), headers=ORIGIN)

    assert previewed.status_code == 200
    assert previewed.json()["affected"] == 2
    assert {row["descriptionRaw"] for row in previewed.json()["sample"]} == {
        "GROCER MARKET",
        "GROCER MARKET TOO",
    }
    assert snapshot(live) == before
    with live.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM classification_rule")
        assert cur.fetchone()[0] == 0  # type: ignore[index]


def test_preview_counts_rows_that_already_have_an_answer_too(live: Fixture) -> None:
    """Preview answers "what does this match", not "what would change". A rule the owner is
    writing to cover a card payment must not read as matching nothing because the row it was
    written for was classified by hand yesterday."""
    a_transaction(live, "GROCER MARKET")
    a_transaction(
        live, "GROCER MARKET TOO", classified_by="user_manual", classification_confidence=1
    )

    assert client().post("/rules/preview", json=rule_body(), headers=ORIGIN).json()["affected"] == 2


def test_classify_with_preview_writes_nothing(live: Fixture) -> None:
    a_transaction(live, "GROCER MARKET")
    client().post("/rules", json=rule_body(setTxnType="income"), headers=ORIGIN)
    before = snapshot(live)

    result = client().post("/classify?preview=true", headers=ORIGIN).json()

    assert (result["considered"], result["changed"], result["applied"]) == (1, 1, False)
    assert snapshot(live) == before


def test_classify_fills_only_the_rows_with_no_answer(live: Fixture) -> None:
    """The invariant the slice is judged on, at the one route that writes."""
    unanswered = a_transaction(live, "GROCER MARKET")
    by_hand = a_transaction(
        live, "GROCER MARKET TOO", classified_by="user_manual", classification_confidence=1
    )
    client().post("/rules", json=rule_body(setTxnType="income"), headers=ORIGIN)

    result = client().post("/classify", headers=ORIGIN).json()

    assert (result["considered"], result["changed"], result["applied"]) == (1, 1, True)
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT id, classified_by::text, txn_type::text, classification_confidence"
            " FROM transaction ORDER BY id"
        )
        answers = {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}
    assert answers[unanswered] == ("user_rule", "income", Decimal("0.980"))
    # Untouched, even though the rule matches it.
    assert answers[by_hand] == ("user_manual", "expense", Decimal("1.000"))


def test_classify_is_not_a_side_effect_of_creating_a_rule(live: Fixture) -> None:
    a_transaction(live, "GROCER MARKET")

    client().post("/rules", json=rule_body(setTxnType="income"), headers=ORIGIN)

    with live.conn.cursor() as cur:
        cur.execute("SELECT classified_by::text FROM transaction")
        assert cur.fetchone()[0] == "unclassified"  # type: ignore[index]


def test_priority_decides_between_two_matching_rules_end_to_end(live: Fixture) -> None:
    a_transaction(live, "GROCER MARKET")
    client().post("/rules", json=rule_body(priority=100, setTxnType="income"), headers=ORIGIN)
    urgent = (
        client()
        .post("/rules", json=rule_body(priority=10, setTxnType="fee"), headers=ORIGIN)
        .json()
    )

    client().post("/classify", headers=ORIGIN)

    with live.conn.cursor() as cur:
        cur.execute("SELECT txn_type::text, classification_rule_id FROM transaction")
        txn_type, rule_id = cur.fetchone()  # type: ignore[misc]
    assert txn_type == "fee"
    assert str(rule_id) == urgent["id"]


def test_a_run_that_applies_a_rule_advances_its_counter(live: Fixture) -> None:
    """V19 marks `times_applied` and `last_applied_at` as the cascade's to maintain, because
    `statement_layout.times_used` is the same shape and nothing ever wrote it."""
    a_transaction(live, "GROCER MARKET")
    a_transaction(live, "GROCER MARKET TOO")
    created = client().post("/rules", json=rule_body(), headers=ORIGIN).json()
    assert created["timesApplied"] == 0

    client().post("/classify", headers=ORIGIN)

    after = client().get("/rules").json()[0]
    assert after["timesApplied"] == 2
    assert after["lastAppliedAt"] is not None


def test_a_preview_run_does_not_advance_the_counter(live: Fixture) -> None:
    a_transaction(live, "GROCER MARKET")
    client().post("/rules", json=rule_body(), headers=ORIGIN)

    client().post("/classify?preview=true", headers=ORIGIN)

    assert client().get("/rules").json()[0]["timesApplied"] == 0


def test_an_inactive_rule_decides_nothing(live: Fixture) -> None:
    a_transaction(live, "GROCER MARKET")
    client().post("/rules", json=rule_body(isActive=False), headers=ORIGIN)

    assert client().post("/classify", headers=ORIGIN).json()["changed"] == 0


def test_a_row_nothing_matches_stays_unclassified_and_says_how_many_were_considered(
    live: Fixture,
) -> None:
    a_transaction(live, "AIRLINE TICKET")
    client().post("/rules", json=rule_body(), headers=ORIGIN)

    result = client().post("/classify", headers=ORIGIN).json()

    assert (result["considered"], result["changed"]) == (1, 0)


def test_preview_and_classify_are_refused_without_a_session() -> None:
    app.dependency_overrides.clear()
    anonymous = TestClient(app)

    assert anonymous.post("/rules/preview", json=rule_body(), headers=ORIGIN).status_code == 401
    assert anonymous.post("/classify", headers=ORIGIN).status_code == 401


def test_an_unknown_transaction_type_is_refused_as_a_client_error(live: Fixture) -> None:
    """The enum in the database is the vocabulary — a second copy here would drift from it —
    but an unknown value is the caller's mistake, not a 500. `accounts.py` does the same."""
    refused = client().post("/rules", json=rule_body(setTxnType="transfer"), headers=ORIGIN)

    assert refused.status_code == 422
    assert "txn_type" in refused.json()["detail"]


def test_an_unknown_payment_method_is_refused_the_same_way(live: Fixture) -> None:
    refused = client().post(
        "/rules", json=rule_body(setPaymentMethod="carrier_pigeon"), headers=ORIGIN
    )

    assert refused.status_code == 422
    assert "payment_method" in refused.json()["detail"]


def test_a_known_transaction_type_is_accepted(live: Fixture) -> None:
    """`transfer_in` and `transfer_out` are the real members; `transfer` is not one, which is
    the mistake the two tests above catch."""
    for known in ("income", "expense", "fee", "transfer_in", "transfer_out"):
        created = client().post("/rules", json=rule_body(setTxnType=known), headers=ORIGIN)
        assert created.status_code == 201, known
