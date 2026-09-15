"""Guarded classification repairs against a disposable migrated database.

Every figure and label is synthetic. The module opens the guarded test connection once because
V23's preview and decision rows are intentionally immutable; pytest drops the whole per-run
database when the session ends.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from threading import Barrier, Event, Lock, get_ident
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from oneledger import auth
from oneledger.main import app
from oneledger.routes import classification_decisions

from .live_database import connect_to_test_database
from .test_ingest_route_live import ORIGIN


@pytest.fixture(scope="module")
def database() -> Iterator[psycopg.Connection[Any]]:
    conn = connect_to_test_database()
    try:
        yield conn
    finally:
        conn.close()


@dataclass
class RepairFixture:
    conn: psycopg.Connection[Any]
    household_id: UUID
    user_id: UUID
    account_id: UUID
    category_id: UUID
    knowledge_version_id: UUID
    currency: str

    def owner(self) -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=self.user_id,
            household_id=self.household_id,
            session_id=UUID(int=3),
            email="repair-owner@example.invalid",
        )


@pytest.fixture
def live(database: psycopg.Connection[Any]) -> Iterator[RepairFixture]:
    with database.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currency, secondary = cur.fetchone()  # type: ignore[misc]
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES (%s, %s, %s) RETURNING id""",
            (f"repair household {uuid4()}", currency, secondary),
        )
        household_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO person (household_id, display_name, is_self) VALUES (%s, %s, true)"
            " RETURNING id",
            (household_id, "synthetic repair owner"),
        )
        person_id = cur.fetchone()[0]  # type: ignore[index]
        user_id = uuid4()
        cur.execute(
            """INSERT INTO app_user (id, household_id, person_id, email, role)
               VALUES (%s, %s, %s, %s, 'owner')""",
            (user_id, household_id, person_id, f"repair-{user_id}@example.invalid"),
        )
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type,
                                     currency, country, opening_balance, opening_balance_date)
               VALUES (%s, %s, %s, 'savings', %s, 'XZ', 0, DATE '2026-01-01')
               RETURNING id""",
            (household_id, person_id, f"repair account {uuid4()}", currency),
        )
        account_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO category (household_id, name, slug, kind, bucket)
               VALUES (%s, 'Synthetic spending', %s, 'expense', 'want') RETURNING id""",
            (household_id, f"synthetic-spending-{uuid4()}"),
        )
        category_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO classification_knowledge (household_id) VALUES (%s) RETURNING id",
            (household_id,),
        )
        knowledge_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO classification_knowledge_version
                 (household_id, knowledge_id, version, source_kind, attribution,
                  evidence_note, matcher_version, scope, state, verified_at)
               VALUES (%s, %s, 1, 'owner', 'synthetic owner', 'reviewed synthetic correction',
                       'repair-v1', '{"direction":"debit"}', 'active', now())
               RETURNING id""",
            (household_id, knowledge_id),
        )
        knowledge_version_id = cur.fetchone()[0]  # type: ignore[index]
    database.commit()
    fixture = RepairFixture(
        database,
        household_id,
        user_id,
        account_id,
        category_id,
        knowledge_version_id,
        str(currency),
    )
    app.dependency_overrides[auth.require_owner] = fixture.owner
    try:
        yield fixture
    finally:
        app.dependency_overrides.clear()
        # V23 records are immutable by design, so the disposable test database is cleaned at
        # the table boundary. CASCADE clears their transaction-dependent rows without adding
        # a production-only delete path; later live fixtures require an empty ledger.
        with database.cursor() as cur:
            cur.execute("TRUNCATE transaction CASCADE")
        database.commit()


def _transaction(
    live: RepairFixture,
    *,
    txn_type: str = "loan_repayment",
    classified_by: str = "seed_rule",
    amount: str = "-37.2500",
    account_id: UUID | None = None,
) -> UUID:
    dedupe = uuid4().hex + uuid4().hex
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction
                 (household_id, account_id, booked_date, description_raw, amount, currency,
                  fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                  amount_secondary, txn_type, source, dedupe_hash, classified_by,
                  classification_confidence, is_reviewed, is_excluded)
               VALUES (%s, %s, DATE '2026-04-01', 'SYNTHETIC WRONG CLASSIFICATION', %s, %s,
                       DATE '2026-04-01', 1, 1, %s, %s, %s::txn_type, 'manual', %s,
                       %s::classified_by, 0.700, %s, true) RETURNING id""",
            (
                live.household_id,
                account_id or live.account_id,
                amount,
                live.currency,
                amount,
                amount,
                txn_type,
                dedupe,
                classified_by,
                classified_by == "user_manual",
            ),
        )
        transaction_id = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(transaction_id))


def _preview(live: RepairFixture, transaction_ids: list[UUID]) -> Response:
    return cast(
        Response,
        TestClient(app).post(
            "/classification-runs/preview",
            json={
                "knowledgeVersionId": str(live.knowledge_version_id),
                "reason": "synthetic wrong exclusion repair",
                "items": [
                    {
                        "transactionId": str(transaction_id),
                        "changes": {
                            "txnType": "expense",
                            "categoryId": str(live.category_id),
                            "classifiedBy": "user_manual",
                            "classificationRuleId": None,
                            "classificationConfidence": "1.000",
                            "isReviewed": True,
                            "isExcluded": False,
                        },
                    }
                    for transaction_id in transaction_ids
                ],
            },
            headers=ORIGIN,
        ),
    )


def _pending_version(live: RepairFixture) -> UUID:
    """A second, brand-new knowledge identity created straight into `pending` — the state a
    guarded review preview creates through `POST /merchant-knowledge?pending=true` before an
    apply either activates or discards it."""
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO classification_knowledge (household_id) VALUES (%s) RETURNING id",
            (live.household_id,),
        )
        knowledge_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO classification_knowledge_version
                 (household_id, knowledge_id, version, source_kind, attribution, evidence_note,
                  matcher_version, scope, state)
               VALUES (%s, %s, 1, 'owner', 'synthetic owner', 'pending synthetic guess',
                       'repair-v1', '{"direction":"debit"}', 'pending') RETURNING id""",
            (live.household_id, knowledge_id),
        )
        version_id = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(version_id))


def _knowledge_state(live: RepairFixture, knowledge_version_id: UUID) -> str:
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT state FROM classification_knowledge_version WHERE id = %s",
            (knowledge_version_id,),
        )
        return str(cur.fetchone()[0])  # type: ignore[index]


def _preview_for(
    live: RepairFixture, transaction_ids: list[UUID], knowledge_version_id: UUID
) -> Response:
    return cast(
        Response,
        TestClient(app).post(
            "/classification-runs/preview",
            json={
                "knowledgeVersionId": str(knowledge_version_id),
                "reason": "synthetic pending knowledge preview",
                "items": [
                    {
                        "transactionId": str(transaction_id),
                        "changes": {"txnType": "expense", "categoryId": str(live.category_id)},
                    }
                    for transaction_id in transaction_ids
                ],
            },
            headers=ORIGIN,
        ),
    )


def _stored(live: RepairFixture, transaction_id: UUID) -> tuple[Any, ...]:
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT txn_type::text, category_id, classified_by::text,
                      classification_confidence, is_reviewed, is_excluded, amount,
                      fx_rate_to_base, amount_base, dedupe_hash, updated_at
                 FROM transaction WHERE id = %s""",
            (transaction_id,),
        )
        return cur.fetchone()  # type: ignore[return-value]


def test_preview_persists_the_complete_guard_and_changes_no_transaction(
    live: RepairFixture,
) -> None:
    transaction_id = _transaction(live)
    before = _stored(live, transaction_id)

    response = _preview(live, [transaction_id])

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["changed"], body["conflicted"], body["unresolved"]) == (
        "proposed",
        1,
        0,
        0,
    )
    [proposal] = body["proposals"]
    assert proposal["transactionId"] == str(transaction_id)
    assert proposal["expectedState"]["txnType"] == "loan_repayment"
    assert proposal["proposedState"]["txnType"] == "expense"
    assert set(proposal["expectedState"]) == {
        "txnType",
        "categoryId",
        "merchantId",
        "paymentMethod",
        "classifiedBy",
        "classificationRuleId",
        "classificationConfidence",
        "isReviewed",
        "country",
        "tags",
        "isExcluded",
    }
    assert _stored(live, transaction_id) == before
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT expected_source_key, expected_transaction_updated_at,
                      expected_state, proposed_state
                 FROM classification_run_item WHERE run_id = %s""",
            (body["runId"],),
        )
        source_key, updated_at, expected, proposed = cur.fetchone()  # type: ignore[misc]
    assert (source_key, updated_at) == (before[9], before[10])
    assert expected["txn_type"] == "loan_repayment"
    assert proposed["txn_type"] == "expense"


def test_preview_serializes_a_confirmed_investment_fee_state(live: RepairFixture) -> None:
    cash_id = _transaction(live, txn_type="fee", amount="-3.0000")
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                     country, opening_balance, opening_balance_date)
               SELECT household_id, owner_person_id, 'synthetic fee investment', 'brokerage',
                      currency, 'XZ', 0, DATE '2026-01-01' FROM account WHERE id = %s
               RETURNING id""",
            (live.account_id,),
        )
        investment_account = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO security (household_id, security_type, name, currency, country,
                                      asset_class)
               VALUES (%s, 'mutual_fund', 'synthetic fee security', %s, 'XZ', 'equity')
               RETURNING id""",
            (live.household_id, live.currency),
        )
        security_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(live.user_id),))
        cur.execute(
            """SELECT append_investment_transaction(%s, %s, DATE '2026-03-01', 'purchase',
                      1, 1, 1, 0, 0, -1, %s, DATE '2026-03-01', 1, 1, -1, -1, 'owner_entered',
                      'classification_fee_v1', %s, NULL, NULL, NULL, NULL)""",
            (investment_account, security_id, live.currency, f"origin-{uuid4()}"),
        )
        origin_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'on', true)")
        cur.execute(
            """INSERT INTO investment_transaction (household_id, account_id, holding_id,
                      security_id, event_date, event_kind, charges, net_amount, currency,
                      fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, net_amount_base,
                      net_amount_secondary, source_kind, source_contract, source_event_id)
               SELECT household_id, account_id, holding_id, security_id, DATE '2026-04-01',
                      'charge', 3, -3, currency, DATE '2026-04-01', 1, 1, -3, -3, 'owner_entered',
                      'classification_fee_v1', %s FROM investment_transaction WHERE id = %s
               RETURNING id""",
            (f"fee-{uuid4()}", origin_id),
        )
        event_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'off', true)")
        cur.execute(
            """SELECT confirm_investment_cash_allocations(ARRAY[%s]::UUID[], ARRAY[%s]::UUID[],
                      ARRAY['fee']::investment_cash_allocation_role[], ARRAY[3]::NUMERIC[])""",
            (event_id, cash_id),
        )
    live.conn.commit()

    response = _preview(live, [cash_id])

    assert response.status_code == 200, response.text
    [proposal] = response.json()["proposals"]
    assert proposal["expectedState"]["txnType"] == "investment_fee"
    assert proposal["proposedState"]["txnType"] == "expense"


def test_preview_rejects_a_complete_proposed_state_over_the_jsonb_byte_limit(
    live: RepairFixture,
) -> None:
    transaction_id = _transaction(live)

    response = TestClient(app, raise_server_exceptions=False).post(
        "/classification-runs/preview",
        json={
            "knowledgeVersionId": str(live.knowledge_version_id),
            "reason": "synthetic unicode state boundary",
            "items": [
                {
                    "transactionId": str(transaction_id),
                    "changes": {"txnType": "expense", "tags": ["界" * 100] * 32},
                }
            ],
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert (response.json()["changed"], response.json()["unresolved"]) == (0, 1)
    assert response.json()["items"][0]["reason"] == "classification state exceeds storage limit"
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM classification_run_item WHERE run_id = %s",
            (response.json()["runId"],),
        )
        assert cur.fetchone()[0] == 0  # type: ignore[index]


def test_preview_rejects_a_complete_expected_state_over_the_escaped_jsonb_limit(
    live: RepairFixture,
) -> None:
    transaction_id = _transaction(live)
    escaped_tag = '\\"' * 50
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE transaction SET tags = %s WHERE id = %s",
            ([escaped_tag] * 32, transaction_id),
        )
    live.conn.commit()

    response = TestClient(app, raise_server_exceptions=False).post(
        "/classification-runs/preview",
        json={
            "knowledgeVersionId": str(live.knowledge_version_id),
            "reason": "synthetic escaped state boundary",
            "items": [{"transactionId": str(transaction_id), "changes": {"txnType": "expense"}}],
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert (response.json()["changed"], response.json()["unresolved"]) == (0, 1)
    assert response.json()["items"][0]["reason"] == "classification state exceeds storage limit"


def test_apply_changes_classification_and_history_but_not_frozen_money(
    live: RepairFixture,
) -> None:
    selected = _transaction(live)
    unrelated = _transaction(live, amount="-12.5000")
    selected_before = _stored(live, selected)
    unrelated_before = _stored(live, unrelated)
    preview = _preview(live, [selected]).json()

    applied = TestClient(app).post(
        f"/classification-runs/{preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-apply-one"},
        headers=ORIGIN,
    )

    assert applied.status_code == 200, applied.text
    assert (applied.json()["state"], applied.json()["changed"]) == ("applied", 1)
    after = _stored(live, selected)
    assert after[:6] == ("expense", live.category_id, "user_manual", Decimal("1.000"), True, False)
    assert after[6:10] == selected_before[6:10]
    assert _stored(live, unrelated) == unrelated_before
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(before_state->>'txn_type'), min(after_state->>'txn_type')"
            " FROM classification_decision WHERE run_id = %s",
            (preview["runId"],),
        )
        assert cur.fetchone() == (1, "loan_repayment", "expense")
        cur.execute(
            "SELECT COALESCE(-sum(amount_base), 0) FROM v_transaction_reportable"
            " WHERE household_id = %s",
            (live.household_id,),
        )
        assert cur.fetchone()[0] == Decimal("37.2500")  # type: ignore[index]


def test_one_stale_item_makes_the_whole_apply_conflict(live: RepairFixture) -> None:
    first, stale = _transaction(live), _transaction(live, amount="-18.7500")
    preview = _preview(live, [first, stale]).json()
    with live.conn.cursor() as cur:
        cur.execute("UPDATE transaction SET txn_type = 'fee' WHERE id = %s", (stale,))
    live.conn.commit()

    response = TestClient(app).post(
        f"/classification-runs/{preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-stale-batch"},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert (response.json()["state"], response.json()["changed"]) == ("conflict", 0)
    assert response.json()["conflicted"] == 1
    assert _stored(live, first)[0] == "loan_repayment"
    assert _stored(live, stale)[0] == "fee"
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM classification_decision WHERE run_id = %s", (preview["runId"],)
        )
        assert cur.fetchone()[0] == 0  # type: ignore[index]


def test_apply_rechecks_the_source_key_and_evidence_lifecycle(live: RepairFixture) -> None:
    source_changed = _transaction(live)
    source_preview = _preview(live, [source_changed]).json()
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE transaction SET dedupe_hash = %s WHERE id = %s",
            (uuid4().hex + uuid4().hex, source_changed),
        )
    live.conn.commit()
    source_result = TestClient(app).post(
        f"/classification-runs/{source_preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-source-conflict"},
        headers=ORIGIN,
    )

    evidence_revoked = _transaction(live, amount="-18.7500")
    evidence_preview = _preview(live, [evidence_revoked]).json()
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE classification_knowledge_version
                  SET state = 'revoked', revoked_at = now()
                WHERE id = %s""",
            (live.knowledge_version_id,),
        )
    live.conn.commit()
    evidence_result = TestClient(app).post(
        f"/classification-runs/{evidence_preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-evidence-conflict"},
        headers=ORIGIN,
    )

    assert source_result.json()["items"][0]["reason"] == "transaction changed after preview"
    assert evidence_result.json()["items"][0]["reason"] == ("knowledge version is no longer active")
    assert _stored(live, source_changed)[0] == "loan_repayment"
    assert _stored(live, evidence_revoked)[0] == "loan_repayment"


def test_apply_keeps_its_stored_result_bounded_when_reasons_are_maximal(
    live: RepairFixture,
) -> None:
    transaction_ids = [_transaction(live, amount=f"-{value}.0000") for value in range(41, 45)]
    preview = (
        TestClient(app)
        .post(
            "/classification-runs/preview",
            json={
                "knowledgeVersionId": str(live.knowledge_version_id),
                "reason": "bounded",
                "items": [
                    {
                        "transactionId": str(transaction_id),
                        "changes": {"txnType": "expense", "isExcluded": False},
                        "reason": "r" * 1000,
                    }
                    for transaction_id in transaction_ids
                ],
            },
            headers=ORIGIN,
        )
        .json()
    )

    response = TestClient(app).post(
        f"/classification-runs/{preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-bounded-result"},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed"] == 4
    assert len(response.json()["items"]) == 3


def test_apply_budgets_unicode_result_samples_and_preserves_exact_history(
    live: RepairFixture,
) -> None:
    transaction_ids = [_transaction(live, amount=f"-{value}.0000") for value in range(45, 48)]
    exact_reason = "界" * 1000
    preview = (
        TestClient(app)
        .post(
            "/classification-runs/preview",
            json={
                "knowledgeVersionId": str(live.knowledge_version_id),
                "reason": "synthetic unicode result boundary",
                "items": [
                    {
                        "transactionId": str(transaction_id),
                        "changes": {"txnType": "expense", "isExcluded": False},
                        "reason": exact_reason,
                    }
                    for transaction_id in transaction_ids
                ],
            },
            headers=ORIGIN,
        )
        .json()
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        f"/classification-runs/{preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-unicode-result"},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed"] == 3
    assert len(response.json()["items"]) == 1
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT octet_length(r.result::text),
                      count(*) FILTER (WHERE i.reason = %s),
                      count(*) FILTER (WHERE d.reason = %s)
                 FROM classification_run r
                 JOIN classification_run_item i ON i.run_id = r.id
                 JOIN classification_decision d
                   ON d.run_id = i.run_id AND d.transaction_id = i.transaction_id
                WHERE r.id = %s
                GROUP BY r.result""",
            (exact_reason, exact_reason, preview["runId"]),
        )
        result_bytes, item_reasons, decision_reasons = cur.fetchone()  # type: ignore[misc]
    assert result_bytes <= 4096
    assert (item_reasons, decision_reasons) == (3, 3)


def test_apply_is_idempotent_and_undo_restores_category_and_type_together(
    live: RepairFixture,
) -> None:
    transaction_id = _transaction(live)
    original = _stored(live, transaction_id)
    preview = _preview(live, [transaction_id]).json()
    apply_url = f"/classification-runs/{preview['runId']}/apply"

    first = TestClient(app).post(
        apply_url, json={"idempotencyKey": "synthetic-repeat"}, headers=ORIGIN
    )
    repeated = TestClient(app).post(
        apply_url, json={"idempotencyKey": "synthetic-repeat"}, headers=ORIGIN
    )
    assert repeated.json() == first.json()

    undone = TestClient(app).post(
        f"/classification-runs/{preview['runId']}/undo",
        json={"idempotencyKey": "synthetic-undo", "reason": "synthetic owner undo"},
        headers=ORIGIN,
    )

    assert undone.status_code == 200, undone.text
    assert (undone.json()["state"], undone.json()["changed"]) == ("applied", 1)
    restored = _stored(live, transaction_id)
    assert restored[:6] == original[:6]
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT count(*), count(supersedes_decision_id)
                 FROM classification_decision WHERE transaction_id = %s""",
            (transaction_id,),
        )
        assert cur.fetchone() == (2, 1)


def test_simultaneous_undo_retries_return_one_result(
    live: RepairFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    transaction_id = _transaction(live)
    source = _preview(live, [transaction_id]).json()
    TestClient(app).post(
        f"/classification-runs/{source['runId']}/apply",
        json={"idempotencyKey": "synthetic-concurrent-source"},
        headers=ORIGIN,
    )
    rendezvous = Barrier(2)
    seen: set[int] = set()
    seen_lock = Lock()
    real_lookup = classification_decisions._idempotency_owner

    def synchronized_lookup(
        cur: psycopg.Cursor[Any], household_id: UUID, key: str
    ) -> tuple[UUID, dict[str, Any]] | None:
        result = real_lookup(cur, household_id, key)
        thread_id = get_ident()
        with seen_lock:
            first_lookup = thread_id not in seen
            seen.add(thread_id)
        if first_lookup:
            rendezvous.wait(timeout=5)
        return result

    monkeypatch.setattr(classification_decisions, "_idempotency_owner", synchronized_lookup)
    url = f"/classification-runs/{source['runId']}/undo"
    body = {"idempotencyKey": "synthetic-concurrent-undo", "reason": "concurrent retry"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [
            pool.submit(TestClient(app).post, url, json=body, headers=ORIGIN) for _ in range(2)
        ]
        responses = [request.result() for request in requests]

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM classification_decision WHERE transaction_id = %s",
            (transaction_id,),
        )
        assert cur.fetchone()[0] == 2  # type: ignore[index]


def test_chain_head_uses_topology_when_undo_transaction_started_before_apply(
    live: RepairFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    transaction_id = _transaction(live)
    source = _preview(live, [transaction_id]).json()
    undo_started = Event()
    allow_undo = Event()
    pause_lock = Lock()
    paused = False
    real_lookup = classification_decisions._idempotency_owner

    def pause_undo_after_its_transaction_starts(
        cur: psycopg.Cursor[Any], household_id: UUID, key: str
    ) -> tuple[UUID, dict[str, Any]] | None:
        nonlocal paused
        result = real_lookup(cur, household_id, key)
        with pause_lock:
            should_pause = key == "synthetic-overlap-undo" and not paused
            if should_pause:
                paused = True
        if should_pause:
            undo_started.set()
            assert allow_undo.wait(timeout=5)
        return result

    monkeypatch.setattr(
        classification_decisions, "_idempotency_owner", pause_undo_after_its_transaction_starts
    )
    undo_url = f"/classification-runs/{source['runId']}/undo"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_undo = pool.submit(
            TestClient(app).post,
            undo_url,
            json={"idempotencyKey": "synthetic-overlap-undo", "reason": "overlapping undo"},
            headers=ORIGIN,
        )
        assert undo_started.wait(timeout=5)
        try:
            applied = TestClient(app).post(
                f"/classification-runs/{source['runId']}/apply",
                json={"idempotencyKey": "synthetic-overlap-apply"},
                headers=ORIGIN,
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["state"] == "applied"
        finally:
            allow_undo.set()
        undone = pending_undo.result(timeout=5)

    assert undone.status_code == 200, undone.text
    assert undone.json()["state"] == "applied"
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT source.created_at, reversal.created_at
                 FROM classification_decision source
                 JOIN classification_decision reversal
                   ON reversal.supersedes_decision_id = source.id
                WHERE source.run_id = %s""",
            (source["runId"],),
        )
        source_created_at, undo_created_at = cur.fetchone()  # type: ignore[misc]
    assert undo_created_at < source_created_at

    fresh = _preview(live, [transaction_id]).json()
    repaired = TestClient(app).post(
        f"/classification-runs/{fresh['runId']}/apply",
        json={"idempotencyKey": "synthetic-after-overlap"},
        headers=ORIGIN,
    )

    assert repaired.status_code == 200, repaired.text
    assert (repaired.json()["state"], repaired.json()["changed"]) == ("applied", 1)


def test_undo_budgets_escaped_result_samples_and_preserves_exact_history(
    live: RepairFixture,
) -> None:
    transaction_ids = [_transaction(live, amount=f"-{value}.0000") for value in range(48, 51)]
    source = _preview(live, transaction_ids).json()
    applied = TestClient(app).post(
        f"/classification-runs/{source['runId']}/apply",
        json={"idempotencyKey": "synthetic-escaped-source"},
        headers=ORIGIN,
    )
    assert applied.status_code == 200, applied.text
    exact_reason = '\\"\n\t' * 250

    response = TestClient(app, raise_server_exceptions=False).post(
        f"/classification-runs/{source['runId']}/undo",
        json={"idempotencyKey": "synthetic-escaped-result", "reason": exact_reason},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed"] == 3
    assert len(response.json()["items"]) == 1
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT octet_length(r.result::text),
                      count(*) FILTER (WHERE i.reason = %s),
                      count(*) FILTER (WHERE d.reason = %s)
                 FROM classification_run r
                 JOIN classification_run_item i ON i.run_id = r.id
                 JOIN classification_decision d
                   ON d.run_id = i.run_id AND d.transaction_id = i.transaction_id
                WHERE r.id = %s
                GROUP BY r.result""",
            (exact_reason, exact_reason, response.json()["runId"]),
        )
        result_bytes, item_reasons, decision_reasons = cur.fetchone()  # type: ignore[misc]
    assert result_bytes <= 4096
    assert (item_reasons, decision_reasons) == (3, 3)


def test_undo_refuses_a_later_correction(live: RepairFixture) -> None:
    transaction_id = _transaction(live)
    first = _preview(live, [transaction_id]).json()
    TestClient(app).post(
        f"/classification-runs/{first['runId']}/apply",
        json={"idempotencyKey": "synthetic-first-correction"},
        headers=ORIGIN,
    )
    later = TestClient(app).post(
        f"/classification-runs/{first['runId']}/undo",
        json={"idempotencyKey": "synthetic-later-correction", "reason": "later correction"},
        headers=ORIGIN,
    )
    assert later.json()["state"] == "applied"

    refused = TestClient(app).post(
        f"/classification-runs/{first['runId']}/undo",
        json={"idempotencyKey": "synthetic-stale-undo", "reason": "too late"},
        headers=ORIGIN,
    )

    assert refused.status_code == 200, refused.text
    assert (refused.json()["state"], refused.json()["changed"]) == ("conflict", 0)
    assert refused.json()["conflicted"] == 1
    assert _stored(live, transaction_id)[0] == "loan_repayment"


def test_manual_decisions_are_unresolved_and_never_enter_the_run_scope(
    live: RepairFixture,
) -> None:
    transaction_id = _transaction(live, classified_by="user_manual")

    response = _preview(live, [transaction_id])

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["changed"], body["unresolved"]) == (0, 1)
    assert body["items"][0]["reason"] == "manual classification is protected"
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM classification_run_item WHERE run_id = %s", (body["runId"],)
        )
        assert cur.fetchone()[0] == 0  # type: ignore[index]


def test_an_unreviewed_manual_decision_can_be_repaired_through_the_guarded_run(
    live: RepairFixture,
) -> None:
    transaction_id = _transaction(live, classified_by="user_manual")
    with live.conn.cursor() as cur:
        cur.execute("UPDATE transaction SET is_reviewed = false WHERE id = %s", (transaction_id,))
    live.conn.commit()

    preview = _preview(live, [transaction_id])

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["changed"], body["unresolved"]) == (1, 0)
    applied = TestClient(app).post(
        f"/classification-runs/{body['runId']}/apply",
        json={"idempotencyKey": "requeued-manual-description-repair"},
        headers=ORIGIN,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["changed"] == 1


def test_a_review_reset_after_an_earlier_correction_keeps_one_continuous_history(
    live: RepairFixture,
) -> None:
    transaction_id = _transaction(live)
    first = _preview(live, [transaction_id]).json()
    applied_first = TestClient(app).post(
        f"/classification-runs/{first['runId']}/apply",
        json={"idempotencyKey": "synthetic-before-review-reset"},
        headers=ORIGIN,
    )
    assert applied_first.json()["state"] == "applied"

    with live.conn.cursor() as cur:
        cur.execute("UPDATE transaction SET is_reviewed = false WHERE id = %s", (transaction_id,))
    live.conn.commit()

    second = _preview(live, [transaction_id]).json()
    applied_second = TestClient(app).post(
        f"/classification-runs/{second['runId']}/apply",
        json={"idempotencyKey": "synthetic-after-review-reset"},
        headers=ORIGIN,
    )

    assert applied_second.status_code == 200, applied_second.text
    assert (applied_second.json()["state"], applied_second.json()["changed"]) == ("applied", 1)
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT count(*), count(supersedes_decision_id), count(DISTINCT run_id)
                 FROM classification_decision WHERE transaction_id = %s""",
            (transaction_id,),
        )
        assert cur.fetchone() == (3, 2, 3)
        cur.execute(
            """SELECT before_state->>'is_reviewed', after_state->>'is_reviewed'
                 FROM classification_decision
                WHERE transaction_id = %s
                  AND reason = %s""",
            (transaction_id, classification_decisions._REVIEW_RESET_REASON),
        )
        assert cur.fetchone() == ("true", "false")


def test_another_households_transaction_is_indistinguishable_from_missing(
    live: RepairFixture,
) -> None:
    with live.conn.cursor() as cur:
        cur.execute("SELECT min(code) FROM currency WHERE code <> %s", (live.currency,))
        secondary = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES (%s, %s, %s) RETURNING id""",
            (f"foreign repair {uuid4()}", live.currency, secondary),
        )
        other_household = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO person (household_id, display_name, is_self) VALUES (%s, %s, true)"
            " RETURNING id",
            (other_household, "foreign synthetic owner"),
        )
        other_person = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type,
                                     currency, country, opening_balance, opening_balance_date)
               VALUES (%s, %s, %s, 'savings', %s, 'XZ', 0, DATE '2026-01-01') RETURNING id""",
            (other_household, other_person, f"foreign account {uuid4()}", live.currency),
        )
        other_account = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO transaction
                 (household_id, account_id, booked_date, description_raw, amount, currency,
                  fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                  amount_secondary, txn_type, source, dedupe_hash, classified_by)
               VALUES (%s, %s, DATE '2026-04-01', 'FOREIGN SYNTHETIC', -1, %s,
                       DATE '2026-04-01', 1, 1, -1, -1, 'expense', 'manual', %s,
                       'unclassified') RETURNING id""",
            (other_household, other_account, live.currency, uuid4().hex + uuid4().hex),
        )
        foreign_transaction = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()

    response = _preview(live, [foreign_transaction])

    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["reason"] == "transaction not found"
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM classification_run_item WHERE run_id = %s",
            (response.json()["runId"],),
        )
        assert cur.fetchone()[0] == 0  # type: ignore[index]


def test_confirmed_transfer_legs_are_unresolved_and_never_relinked(live: RepairFixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type,
                                     currency, country, opening_balance, opening_balance_date)
               SELECT %s, owner_person_id, %s, 'savings', currency, 'XZ', 0, DATE '2026-01-01'
                 FROM account WHERE id = %s RETURNING id""",
            (live.household_id, f"transfer peer {uuid4()}", live.account_id),
        )
        peer_account = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    debit = _transaction(live)
    credit = _transaction(live, txn_type="income", amount="37.2500", account_id=peer_account)
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transfer
                 (household_id, source_account_id, destination_account_id,
                  source_transaction_id, destination_transaction_id, source_amount,
                  source_currency, destination_amount, destination_currency, transfer_date,
                  transfer_kind, match_method)
               VALUES (%s, %s, %s, %s, %s, 37.2500, %s, 37.2500, %s, DATE '2026-04-01',
                       'internal_same_currency', 'user_created') RETURNING id""",
            (
                live.household_id,
                live.account_id,
                peer_account,
                debit,
                credit,
                live.currency,
                live.currency,
            ),
        )
        transfer_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(live.user_id),))
        cur.execute("SELECT confirm_transfer(%s)", (transfer_id,))
    live.conn.commit()

    response = _preview(live, [debit])

    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["reason"] == "confirmed transfer leg is protected"
    with live.conn.cursor() as cur:
        cur.execute("SELECT transfer_id, txn_type::text FROM transaction WHERE id = %s", (debit,))
        assert cur.fetchone() == (transfer_id, "transfer_out")


def test_preview_accepts_pending_knowledge_and_apply_activates_it_by_default(
    live: RepairFixture,
) -> None:
    version_id = _pending_version(live)
    transaction_id = _transaction(live)

    preview = _preview_for(live, [transaction_id], version_id)
    assert preview.status_code == 200, preview.text
    assert preview.json()["changed"] == 1
    assert _knowledge_state(live, version_id) == "pending"

    applied = TestClient(app).post(
        f"/classification-runs/{preview.json()['runId']}/apply",
        json={"idempotencyKey": "synthetic-pending-default-activate"},
        headers=ORIGIN,
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "applied"
    assert _knowledge_state(live, version_id) == "active"


def test_apply_with_activate_knowledge_false_leaves_the_version_pending(
    live: RepairFixture,
) -> None:
    version_id = _pending_version(live)
    transaction_id = _transaction(live)
    preview = _preview_for(live, [transaction_id], version_id).json()

    applied = TestClient(app).post(
        f"/classification-runs/{preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-pending-no-activate", "activateKnowledge": False},
        headers=ORIGIN,
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "applied"
    assert _knowledge_state(live, version_id) == "pending"


def test_a_conflicted_apply_leaves_pending_knowledge_pending(live: RepairFixture) -> None:
    version_id = _pending_version(live)
    transaction_id = _transaction(live)
    preview = _preview_for(live, [transaction_id], version_id).json()
    with live.conn.cursor() as cur:
        cur.execute("UPDATE transaction SET txn_type = 'fee' WHERE id = %s", (transaction_id,))
    live.conn.commit()

    applied = TestClient(app).post(
        f"/classification-runs/{preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-pending-conflict"},
        headers=ORIGIN,
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "conflict"
    assert _knowledge_state(live, version_id) == "pending"


def test_only_a_revoked_knowledge_version_conflicts_a_pending_apply(live: RepairFixture) -> None:
    version_id = _pending_version(live)
    transaction_id = _transaction(live)
    preview = _preview_for(live, [transaction_id], version_id).json()
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE classification_knowledge_version SET state = 'revoked', revoked_at = now()"
            " WHERE id = %s",
            (version_id,),
        )
    live.conn.commit()

    applied = TestClient(app).post(
        f"/classification-runs/{preview['runId']}/apply",
        json={"idempotencyKey": "synthetic-revoked-conflict"},
        headers=ORIGIN,
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["items"][0]["reason"] == "knowledge version is no longer active"


def test_runs_list_is_scoped_paged_excludes_bridges_and_marks_undone(
    live: RepairFixture,
) -> None:
    first = _transaction(live)
    second = _transaction(live, amount="-18.7500")
    first_applied = (
        TestClient(app)
        .post(
            f"/classification-runs/{_preview(live, [first]).json()['runId']}/apply",
            json={"idempotencyKey": "synthetic-runs-list-first"},
            headers=ORIGIN,
        )
        .json()
    )
    second_applied = (
        TestClient(app)
        .post(
            f"/classification-runs/{_preview(live, [second]).json()['runId']}/apply",
            json={"idempotencyKey": "synthetic-runs-list-second"},
            headers=ORIGIN,
        )
        .json()
    )
    TestClient(app).post(
        f"/classification-runs/{first_applied['runId']}/undo",
        json={"idempotencyKey": "synthetic-runs-list-undo", "reason": "owner undid it"},
        headers=ORIGIN,
    )

    listed = TestClient(app).get(
        "/classification-runs", params={"limit": 200, "offset": 0}, headers=ORIGIN
    )
    assert listed.status_code == 200, listed.text
    by_id = {row["runId"]: row for row in listed.json()}
    assert by_id[first_applied["runId"]]["undone"] is True
    assert by_id[second_applied["runId"]]["undone"] is False
    assert by_id[first_applied["runId"]]["reason"] != classification_decisions._REVIEW_RESET_REASON
    undo_row = next(row for row in listed.json() if row["sourceRunId"] == first_applied["runId"])
    assert undo_row["changed"] == 1

    paged = (
        TestClient(app)
        .get("/classification-runs", params={"limit": 1, "offset": 0}, headers=ORIGIN)
        .json()
    )
    assert len(paged) == 1


def test_runs_list_requires_an_owner_session_and_is_household_scoped(
    live: RepairFixture,
) -> None:
    app.dependency_overrides.clear()
    try:
        unauthenticated = TestClient(app).get("/classification-runs")
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner
    assert unauthenticated.status_code == 401

    transaction_id = _transaction(live)
    TestClient(app).post(
        f"/classification-runs/{_preview(live, [transaction_id]).json()['runId']}/apply",
        json={"idempotencyKey": "synthetic-runs-list-household"},
        headers=ORIGIN,
    )

    other_household = auth.OwnerSession(
        user_id=uuid4(),
        household_id=uuid4(),
        session_id=UUID(int=9),
        email="other-household@example.invalid",
    )
    app.dependency_overrides[auth.require_owner] = lambda: other_household
    try:
        foreign = TestClient(app).get("/classification-runs", headers=ORIGIN)
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner
    assert foreign.status_code == 200, foreign.text
    assert foreign.json() == []
