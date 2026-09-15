"""Live tests for UI completion Task 2a: owner-entered valuation snapshots and valuation-mode
transitions (`docs/superpowers/plans/2026-09-15-ui-completion.md#task-2a`).

A synthetic household with two accounts: one `retirement` account (snapshot mode from its
initial period) and one `savings` account (ledger_balance mode, used for the wrong-mode case),
plus a foreign household's own retirement account for the not-found case. One exact fx_rate row
(secondary per base = 0.5) makes the frozen FX figures exactly checkable. Skipped when no
database is reachable. Every name, label and amount is synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.auth import require_owner
from oneledger.main import app

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}
RATE_DATE = date(2026, 3, 1)


@dataclass(frozen=True)
class ValuationFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    account_id: UUID
    wrong_mode_account_id: UUID
    other_account_id: UUID
    currency: str
    secondary_currency: str


def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def live() -> Iterator[ValuationFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "valuation-owner@example.invalid")
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = str(found[0]).strip(), str(found[1]).strip()
        for household, name in (
            (household_id, "valuation household"),
            (other_household_id, "other valuation household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES (%s, %s, %s, 0.5000000000, 'manual')
               ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                 SET rate = EXCLUDED.rate, source = EXCLUDED.source""",
            (RATE_DATE, secondary, base),
        )
        for person, household, name in (
            (person_id, household_id, "valuation owner"),
            (other_person_id, other_household_id, "other valuation owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, owner.email),
            (other_user_id, other_household_id, other_person_id, "other-valuation@example.invalid"),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )

        def account(household: UUID, person: UUID, name: str, account_type: str) -> UUID:
            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
                   VALUES (%s, %s, %s, %s::account_type, %s, 'XZ', 0, '2026-01-01')
                   RETURNING id""",
                (household, person, name, account_type, base),
            )
            row = cur.fetchone()
            assert row is not None
            return UUID(str(row[0]))

        account_id = account(household_id, person_id, "Synthetic retirement account", "retirement")
        wrong_mode_account_id = account(
            household_id, person_id, "Synthetic savings account", "savings"
        )
        other_account_id = account(
            other_household_id, other_person_id, "Other synthetic retirement account", "retirement"
        )
    connection.commit()

    app.dependency_overrides[require_owner] = lambda: owner
    try:
        yield ValuationFixture(
            connection=connection,
            owner=owner,
            account_id=account_id,
            wrong_mode_account_id=wrong_mode_account_id,
            other_account_id=other_account_id,
            currency=base,
            secondary_currency=secondary,
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _snapshot_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "asOfDate": str(RATE_DATE),
        "valueNative": "1000.0000",
        "method": "owner_entered",
        "confidence": "high",
    }
    body.update(overrides)
    return body


def test_create_snapshot_freezes_fx_exactly(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(),
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["valueNative"] == "1000.0000"
    assert body["currency"] == live.currency
    assert body["fxRateDate"] == str(RATE_DATE)
    assert body["fxRateToBase"] == "1.0000000000"
    assert body["fxRateToSecondary"] == "2.0000000000"
    assert body["valueBase"] == "1000.0000"
    assert body["valueSecondary"] == "2000.0000"
    assert body["method"] == "owner_entered"
    assert body["confidence"] == "high"
    assert body["sourceNote"] is None

    listed = client().get(f"/accounts/{live.account_id}/valuations", headers=ORIGIN)
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["currentMode"] == "snapshot"
    assert [s["id"] for s in listed_body["snapshots"]] == [body["id"]]


def test_identical_replay_returns_200_with_the_stored_row(live: ValuationFixture) -> None:
    first = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots", json=_snapshot_body(), headers=ORIGIN
    )
    assert first.status_code == 201, first.text
    second = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots", json=_snapshot_body(), headers=ORIGIN
    )
    assert second.status_code == 200, second.text
    assert second.json() == first.json()


def test_different_value_on_the_same_date_is_409(live: ValuationFixture) -> None:
    first = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots", json=_snapshot_body(), headers=ORIGIN
    )
    assert first.status_code == 201, first.text
    second = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(valueNative="1000.0001"),
        headers=ORIGIN,
    )
    assert second.status_code == 409, second.text


def test_wrong_mode_is_422(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.wrong_mode_account_id}/valuation-snapshots",
        json=_snapshot_body(),
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text


def test_future_date_is_422(live: ValuationFixture) -> None:
    tomorrow = date.today() + timedelta(days=1)
    response = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(asOfDate=str(tomorrow)),
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text


def test_excess_precision_is_422(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(valueNative="1000.00001"),
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text


def test_screened_note_is_422(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(sourceNote="PIN 1234"),
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "the source note must not contain private information"


def test_foreign_account_is_404(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.other_account_id}/valuation-snapshots",
        json=_snapshot_body(),
        headers=ORIGIN,
    )
    assert response.status_code == 404, response.text
    listed = client().get(f"/accounts/{live.other_account_id}/valuations", headers=ORIGIN)
    assert listed.status_code == 404


def _coverage_for(body: dict[str, Any], account_id: UUID) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = body["netWorth"]["investmentCoverage"]
    return [row for row in coverage if row["accountId"] == str(account_id)]


def test_dashboard_net_worth_available_once_a_snapshot_exists(live: ValuationFixture) -> None:
    before = client().get("/dashboard", params={"asOf": str(RATE_DATE)}, headers=ORIGIN).json()
    coverage_before = _coverage_for(before, live.account_id)
    assert coverage_before == [
        {
            "accountId": str(live.account_id),
            "holdingId": None,
            "valuationMode": "snapshot",
            "status": "missing_snapshot",
            "valueDate": None,
            "carried": False,
            "ageDays": None,
            "method": None,
            "confidence": None,
        }
    ]

    created = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots", json=_snapshot_body(), headers=ORIGIN
    )
    assert created.status_code == 201, created.text

    after = client().get("/dashboard", params={"asOf": str(RATE_DATE)}, headers=ORIGIN).json()
    coverage_after = _coverage_for(after, live.account_id)
    assert coverage_after[0]["status"] == "current_snapshot"
    assert coverage_after[0]["valueDate"] == str(RATE_DATE)


# --- valuation-mode transition -----------------------------------------------------------


def test_mode_transition_success_and_identical_retry(live: ValuationFixture) -> None:
    transition_date = str(RATE_DATE)
    first = client().post(
        f"/accounts/{live.wrong_mode_account_id}/valuation-mode",
        json={"valuationMode": "holdings", "effectiveFrom": transition_date},
        headers=ORIGIN,
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "accountId": str(live.wrong_mode_account_id),
        "valuationMode": "holdings",
        "effectiveFrom": transition_date,
    }

    retry = client().post(
        f"/accounts/{live.wrong_mode_account_id}/valuation-mode",
        json={"valuationMode": "holdings", "effectiveFrom": transition_date},
        headers=ORIGIN,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == first.json()

    same_mode_different_date = client().post(
        f"/accounts/{live.wrong_mode_account_id}/valuation-mode",
        json={"valuationMode": "holdings", "effectiveFrom": "2026-04-01"},
        headers=ORIGIN,
    )
    assert same_mode_different_date.status_code == 409, same_mode_different_date.text


def test_mode_transition_liability_refusal_is_422(live: ValuationFixture) -> None:
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO account (
                   household_id, owner_person_id, name, account_type, account_class, currency,
                   country, opening_balance, opening_balance_date, credit_limit)
               VALUES (%s, (SELECT owner_person_id FROM account WHERE id = %s),
                       'Synthetic credit card', 'credit_card', 'liability', %s, 'XZ', 0,
                       '2026-01-01', 1000) RETURNING id""",
            (live.owner.household_id, live.account_id, live.currency),
        )
        row = cur.fetchone()
        assert row is not None
        liability_account_id = UUID(str(row[0]))
    live.connection.commit()

    response = client().post(
        f"/accounts/{liability_account_id}/valuation-mode",
        json={"valuationMode": "snapshot", "effectiveFrom": str(RATE_DATE)},
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text


def test_mode_transition_before_current_period_start_is_422(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.wrong_mode_account_id}/valuation-mode",
        json={"valuationMode": "holdings", "effectiveFrom": "2025-01-01"},
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text


def test_mode_transition_would_strand_snapshots_is_422(live: ValuationFixture) -> None:
    created = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots", json=_snapshot_body(), headers=ORIGIN
    )
    assert created.status_code == 201, created.text

    response = client().post(
        f"/accounts/{live.account_id}/valuation-mode",
        json={"valuationMode": "holdings", "effectiveFrom": str(RATE_DATE)},
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text


def test_mode_transition_foreign_account_is_404(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.other_account_id}/valuation-mode",
        json={"valuationMode": "holdings", "effectiveFrom": str(RATE_DATE)},
        headers=ORIGIN,
    )
    assert response.status_code == 404, response.text


# --- owner-only boundary ------------------------------------------------------------------
#
# There is no separate member-session issuance path to forge a session against (`require_owner`
# only resolves sessions whose `app_user.role = 'owner'`, V5's session lookup), so the boundary
# these three routes must honour is that every one of them depends on `require_owner` — the same
# generic check `test_health.py::test_every_current_non_bootstrap_route_depends_on_the_owner`
# already runs over the whole app. This asserts it directly for the new paths.


def _current_api_routes() -> list[APIRoute]:
    # Mirrors test_health.py's own helper: newer FastAPI wraps an included router in
    # `_IncludedRouter`, so `app.routes` itself holds no plain `APIRoute` objects.
    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
        elif hasattr(route, "original_router"):
            routes.extend(r for r in route.original_router.routes if isinstance(r, APIRoute))
    return routes


def test_valuation_routes_require_the_owner() -> None:
    routes = _current_api_routes()
    covered = {
        (r.path, method)
        for r in routes
        for method in (r.methods or ())
        if r.path.startswith("/accounts/{account_id}/valuation")
    }
    assert covered == {
        ("/accounts/{account_id}/valuations", "GET"),
        ("/accounts/{account_id}/valuation-snapshots", "POST"),
        ("/accounts/{account_id}/valuation-mode", "POST"),
    }
    for route in routes:
        if route.path.startswith("/accounts/{account_id}/valuation"):
            assert any(d.call is require_owner for d in route.dependant.dependencies), route.path


def test_no_session_is_401(live: ValuationFixture) -> None:
    app.dependency_overrides.clear()
    try:
        response = TestClient(app).post(
            f"/accounts/{live.account_id}/valuation-snapshots",
            json=_snapshot_body(),
            headers=ORIGIN,
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides[require_owner] = lambda: live.owner


def test_precision_is_validated(live: ValuationFixture) -> None:
    # A negative amount is refused too: value_native is CHECKed non-negative, and the wire
    # pattern (unlike money.MONEY) carries no sign.
    response = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(valueNative="-1.0000"),
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text


def test_confidence_and_method_are_screened(live: ValuationFixture) -> None:
    response = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(method="market_quote"),
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text
    response = client().post(
        f"/accounts/{live.account_id}/valuation-snapshots",
        json=_snapshot_body(confidence="extreme"),
        headers=ORIGIN,
    )
    assert response.status_code == 422, response.text
