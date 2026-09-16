"""Phase 4.1 Task 2: `GET/POST/PATCH/DELETE /budgets` and `POST /budgets/copy` (spec "Budgets are
owner assumptions, not postings").

A budget line posts nothing and never becomes income or spending — every scenario below reads
`GET /budgets` after writing directly to the ledger, never the other way round. The rollover chain
is proven month by month, including a bucket line excluding a category that carries its own line
only in some of the months it visits.

Skipped when no database is reachable. Every name, label and amount is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, _clean, _connect
from .test_spending_live import _another_household, a_category, a_spend
from .test_transfer_writes_live import Owner
from .test_transfers_live import client


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Owner]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
    fixture: Owner | None = None
    try:
        fixture = Owner(conn)
        # `Fixture.__init__` reuses the first seeded household rather than creating a fresh one,
        # so `budget_line` (new to this phase; no other suite writes it) is truncated wholesale
        # here rather than filtered by household — the household itself is shared across tests.
        with conn.cursor() as cur:
            cur.execute("TRUNCATE budget_line")
        conn.commit()
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            cur.execute("TRUNCATE budget_line")
            cur.execute("DELETE FROM category WHERE name LIKE 'Synthetic budgets%'")
            if fixture is not None:
                cur.execute("DELETE FROM app_user WHERE id = %s", (fixture.user_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def a_budget_line(
    live: Owner,
    month: date,
    *,
    category_id: UUID | None = None,
    bucket: str | None = None,
    amount: str = "100.0000",
    rollover: bool = False,
) -> UUID:
    """A line written straight to the table — the rollover-chain fixtures need many months of
    these, and only the month under test is exercised through the route."""
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id,
                                         bucket, amount_base, currency, rollover_enabled)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                live.household_id,
                uuid4(),
                month,
                category_id,
                bucket,
                amount,
                live.base,
                rollover,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def the_budgets(month: date) -> dict[str, Any]:
    response = client().get("/budgets", params={"month": str(month)}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def line_for(
    body: dict[str, Any], *, category_id: UUID | None = None, bucket: str | None = None
) -> dict[str, Any]:
    key = None if category_id is None else str(category_id)
    return next(
        row for row in body["lines"] if row["categoryId"] == key and row["bucket"] == bucket
    )


def create_line(
    idempotency_key: UUID,
    month: date,
    *,
    category_id: UUID | None = None,
    bucket: str | None = None,
    amount: str = "100.0000",
    rollover: bool = False,
    note: str | None = None,
) -> httpx.Response:
    response: httpx.Response = client().post(
        "/budgets",
        json={
            "idempotencyKey": str(idempotency_key),
            "periodMonth": str(month),
            "categoryId": None if category_id is None else str(category_id),
            "bucket": bucket,
            "amountBase": amount,
            "rolloverEnabled": rollover,
            "note": note,
        },
        headers=ORIGIN,
    )
    return response


def created_line(
    month: date,
    *,
    category_id: UUID | None = None,
    bucket: str | None = None,
    amount: str = "100.0000",
    rollover: bool = False,
) -> httpx.Response:
    """`create_line`, asserting the 201 every setup call in this suite expects."""
    response = create_line(
        uuid4(), month, category_id=category_id, bucket=bucket, amount=amount, rollover=rollover
    )
    assert response.status_code == 201, response.text
    return response


# ---------------------------------------------------------------------------------------


def test_variance_without_rollover(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets groceries", "need")
    month = date(2026, 3, 1)
    response = create_line(uuid4(), month, category_id=groceries, amount="100.0000")
    assert response.status_code == 201, response.text
    a_spend(live, live.account_id, "-60", date(2026, 3, 5), "expense", category_id=groceries)

    body = the_budgets(month)
    row = line_for(body, category_id=groceries)
    assert row["amountBase"] == "100.0000"
    assert row["actualBase"] == "60.0000"
    assert row["carriedBase"] == "0.0000"
    assert row["availableBase"] == "100.0000"
    assert row["varianceBase"] == "40.0000"
    assert row["carryMonths"] == 0
    assert row["carryStoppedReason"] == "rollover_off"
    assert body["totals"]["varianceBase"] == "40.0000"


def test_carry_across_two_months_each_with_its_own_excluded_categories(live: Owner) -> None:
    """`want` is budgeted as a bucket in both January and February; dining carries its own line
    only in February, so January's actual for `want` includes dining and February's does not —
    proving the chain recomputes each month's exclusion set rather than reusing the requested
    month's."""
    dining = a_category(live, "Synthetic budgets dining", "want")
    jan, feb = date(2026, 1, 1), date(2026, 2, 1)

    created_line(jan, bucket="want", amount="100.0000", rollover=True)
    a_spend(live, live.account_id, "-40", date(2026, 1, 10), "expense", category_id=dining)
    # January has no December line: the first read proves carryMonths starts at 0.
    jan_body = the_budgets(jan)
    jan_row = line_for(jan_body, bucket="want")
    assert jan_row["carryMonths"] == 0
    assert jan_row["carryStoppedReason"] == "no_prior_line"
    assert jan_row["actualBase"] == "40.0000"
    assert jan_row["availableBase"] == "100.0000"

    created_line(feb, bucket="want", amount="100.0000", rollover=True)
    created_line(feb, category_id=dining, amount="50.0000")
    a_spend(live, live.account_id, "-40", date(2026, 2, 10), "expense", category_id=dining)
    a_spend(live, live.account_id, "-15", date(2026, 2, 11), "expense", category_id=None)

    feb_body = the_budgets(feb)
    feb_want = line_for(feb_body, bucket="want")
    # February's actual for `want` excludes dining (it now carries its own line): only the
    # uncategorised-under-want... there is none here, so `want`'s only contributor left is none —
    # instead this proves the excluded id is reported and January's carry (100 - 40 = 60) still
    # lands in February's available amount.
    assert feb_want["excludedCategoryIds"] == [str(dining)]
    assert feb_want["carriedBase"] == "60.0000"
    assert feb_want["carryMonths"] == 1
    assert feb_want["carryStoppedReason"] == "no_prior_line"
    assert feb_want["availableBase"] == "160.0000"


def test_carry_stops_at_rollover_off(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets groceries stop", "need")
    dec, jan, feb = date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)
    a_budget_line(live, dec, category_id=groceries, amount="10.0000", rollover=False)
    a_budget_line(live, jan, category_id=groceries, amount="20.0000", rollover=True)
    created_line(feb, category_id=groceries, amount="30.0000", rollover=True)

    body = the_budgets(feb)
    row = line_for(body, category_id=groceries)
    # December has rollover disabled, so the chain includes it (its own remainder still lands in
    # January's available amount) but does not look past it into November.
    assert row["carryMonths"] == 2
    assert row["carryStoppedReason"] == "rollover_off"
    assert row["carriedBase"] == "30.0000"
    assert row["availableBase"] == "60.0000"


def test_carry_stops_at_the_twelve_month_bound(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets groceries bound", "need")
    month = date(2027, 1, 1)
    for back in range(1, 13):
        year, mon = month.year, month.month - back
        while mon <= 0:
            mon += 12
            year -= 1
        a_budget_line(
            live, date(year, mon, 1), category_id=groceries, amount="10.0000", rollover=True
        )
    created_line(month, category_id=groceries, amount="10.0000", rollover=True)

    body = the_budgets(month)
    row = line_for(body, category_id=groceries)
    assert row["carryMonths"] == 12
    assert row["carryStoppedReason"] == "bound_reached"
    assert row["carriedBase"] == "120.0000"


def test_bucket_line_excludes_a_category_lined_child(live: Owner) -> None:
    dining = a_category(live, "Synthetic budgets dining child", "want")
    shopping = a_category(live, "Synthetic budgets shopping child", "want")
    month = date(2026, 5, 1)
    assert create_line(uuid4(), month, category_id=dining, amount="50.0000").status_code == 201
    assert create_line(uuid4(), month, bucket="want", amount="200.0000").status_code == 201
    a_spend(live, live.account_id, "-30", date(2026, 5, 5), "expense", category_id=dining)
    a_spend(live, live.account_id, "-20", date(2026, 5, 6), "expense", category_id=shopping)

    body = the_budgets(month)
    bucket_row = line_for(body, bucket="want")
    assert bucket_row["excludedCategoryIds"] == [str(dining)]
    assert bucket_row["actualBase"] == "20.0000"
    dining_row = line_for(body, category_id=dining)
    assert dining_row["actualBase"] == "30.0000"


def test_replay_200_and_conflict_409(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets replay", "need")
    month = date(2026, 3, 1)
    key = uuid4()
    first = create_line(key, month, category_id=groceries, amount="100.0000")
    assert first.status_code == 201, first.text
    line_id = first.json()["id"]

    replay = create_line(key, month, category_id=groceries, amount="100.0000")
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == line_id

    conflict = create_line(key, month, category_id=groceries, amount="200.0000")
    assert conflict.status_code == 409, conflict.text


def test_duplicate_scope_conflict(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets duplicate", "need")
    month = date(2026, 3, 1)
    assert create_line(uuid4(), month, category_id=groceries, amount="100.0000").status_code == 201
    duplicate = create_line(uuid4(), month, category_id=groceries, amount="150.0000")
    assert duplicate.status_code == 409, duplicate.text


def test_inactive_category_is_refused(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets inactive", "need")
    with live.conn.cursor() as cur:
        cur.execute("UPDATE category SET is_active = false WHERE id = %s", (groceries,))
    live.conn.commit()
    response = create_line(uuid4(), date(2026, 3, 1), category_id=groceries, amount="100.0000")
    assert response.status_code == 422, response.text


def test_unknown_category_is_refused(live: Owner) -> None:
    response = create_line(uuid4(), date(2026, 3, 1), category_id=uuid4(), amount="100.0000")
    assert response.status_code == 422, response.text


def test_foreign_category_is_refused(live: Owner) -> None:
    theirs, _account = _another_household(live)
    foreign = a_category(live, "Synthetic budgets foreign", "need", household=theirs)
    response = create_line(uuid4(), date(2026, 3, 1), category_id=foreign, amount="100.0000")
    assert response.status_code == 404, response.text


def test_update_amount_rollover_and_note_but_not_scope(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets update", "need")
    month = date(2026, 3, 1)
    created = create_line(uuid4(), month, category_id=groceries, amount="100.0000", note="first")
    assert created.status_code == 201, created.text
    line_id = created.json()["id"]

    updated = client().patch(
        f"/budgets/{line_id}",
        json={"amountBase": "150.0000", "rolloverEnabled": True, "note": "revised"},
        headers=ORIGIN,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["amountBase"] == "150.0000"
    assert updated.json()["rolloverEnabled"] is True
    assert updated.json()["note"] == "revised"

    scope_change = client().patch(
        f"/budgets/{line_id}",
        json={
            "amountBase": "150.0000",
            "rolloverEnabled": True,
            "note": "revised",
            "categoryId": str(groceries),
        },
        headers=ORIGIN,
    )
    assert scope_change.status_code == 422, scope_change.text


def test_delete_budget_line(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets delete", "need")
    created = create_line(uuid4(), date(2026, 3, 1), category_id=groceries, amount="100.0000")
    line_id = created.json()["id"]
    assert client().delete(f"/budgets/{line_id}", headers=ORIGIN).status_code == 204
    assert client().delete(f"/budgets/{line_id}", headers=ORIGIN).status_code == 404


def test_copy_skips_existing_scopes_and_is_idempotent(live: Owner) -> None:
    dining = a_category(live, "Synthetic budgets copy", "want")
    jan, feb = date(2026, 1, 1), date(2026, 2, 1)
    assert create_line(uuid4(), jan, category_id=dining, amount="50.0000").status_code == 201
    assert create_line(uuid4(), jan, bucket="want", amount="200.0000").status_code == 201
    # Already present in February — the copy must skip this scope, not overwrite it.
    assert create_line(uuid4(), feb, category_id=dining, amount="999.0000").status_code == 201

    copy_key = uuid4()
    first = client().post(
        "/budgets/copy",
        json={"fromMonth": str(jan), "toMonth": str(feb), "idempotencyKey": str(copy_key)},
        headers=ORIGIN,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["skippedCount"] == 1
    assert len(body["created"]) == 1
    assert body["created"][0]["bucket"] == "want"

    feb_body = the_budgets(feb)
    assert line_for(feb_body, category_id=dining)["amountBase"] == "999.0000"
    assert line_for(feb_body, bucket="want")["amountBase"] == "200.0000"

    replay = client().post(
        "/budgets/copy",
        json={"fromMonth": str(jan), "toMonth": str(feb), "idempotencyKey": str(copy_key)},
        headers=ORIGIN,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == {"created": [], "skippedCount": 2}


def test_foreign_household_refusals(live: Owner) -> None:
    groceries = a_category(live, "Synthetic budgets foreign household", "need")
    month = date(2026, 3, 1)
    created = create_line(uuid4(), month, category_id=groceries, amount="100.0000")
    line_id = created.json()["id"]

    theirs, _account = _another_household(live)
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(),
        household_id=theirs,
        session_id=uuid4(),
        email="other-budgets-owner@example.invalid",
    )
    try:
        assert the_budgets(month)["lines"] == []
        assert (
            client()
            .patch(
                f"/budgets/{line_id}",
                json={"amountBase": "1.0000", "rolloverEnabled": False, "note": None},
                headers=ORIGIN,
            )
            .status_code
            == 404
        )
        assert client().delete(f"/budgets/{line_id}", headers=ORIGIN).status_code == 404
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner


def test_routes_require_owner_session() -> None:
    app.dependency_overrides.clear()
    assert (
        client().get("/budgets", params={"month": "2026-03-01"}, headers=ORIGIN).status_code == 401
    )
    assert (
        client()
        .post(
            "/budgets",
            json={
                "idempotencyKey": str(uuid4()),
                "periodMonth": "2026-03-01",
                "categoryId": None,
                "bucket": "need",
                "amountBase": "10.0000",
                "rolloverEnabled": False,
                "note": None,
            },
            headers=ORIGIN,
        )
        .status_code
        == 401
    )
