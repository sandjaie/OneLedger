"""Phase 2.2 Task 2: `GET /wealth-history/{monthEnd}/attribution` (spec "API").

One small household: a base-currency ledger account opened before the history anchor's own
opening point, with one reportable income posting inside January 2024. `01__household` here
means every number is hand-derivable, so the live route's result is checked two ways — against
literal expected strings, and against calling the pure `attribution.attribute` directly with the
same fixture's own facts as typed inputs — proving the route's SQL loading (stored positions, the
window's frozen posting, the closing rate) reproduces the pure function's own result, not just a
plausible-looking one. A second, otherwise empty household proves isolation. Skipped when no
database is reachable. Every name and amount is synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import attribution, auth
from oneledger.main import app
from oneledger.routes.transactions import post_ledger_transaction

from .live_database import clear_synthetic_account_valuation_periods, connect_to_test_database
from .test_wealth_history_live import _account, _secondary_to_base_rate

ORIGIN = {"Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class AttributionFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    base: str
    secondary: str
    account_id: UUID


def _client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def live() -> Iterator[AttributionFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "attribution-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-attribution-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = str(found[0]).strip(), str(found[1]).strip()

        for household, name in (
            (household_id, "attribution household"),
            (other_household_id, "other attribution household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "attribution owner"),
            (other_person_id, other_household_id, "other attribution owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, owner.email),
            (other_user_id, other_household_id, other_person_id, other_owner.email),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )

        # A constant secondary-to-base rate covers both month ends' carried-forward lookup and
        # the income posting's own exact-date freeze — no rate move, since that bridge is already
        # proven by the pure unit fixture (tests/test_attribution.py).
        _secondary_to_base_rate(cur, date(2023, 12, 1), secondary, base, "2.0000000000")
        _secondary_to_base_rate(cur, date(2024, 1, 15), secondary, base, "2.0000000000")

        account_id = _account(
            cur,
            household_id,
            person_id,
            "savings",
            base,
            opened_on=date(2023, 12, 1),
            opening_balance="1000",
            opening_balance_date=date(2023, 12, 1),
        )
        post_ledger_transaction(
            cur,
            household_id,
            account_id,
            date(2024, 1, 15),
            "Synthetic attribution income",
            Decimal("200"),
            "income",
            None,
        )
    connection.commit()

    try:
        yield AttributionFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            base=base,
            secondary=secondary,
            account_id=account_id,
        )
    finally:
        with connection.cursor() as cur:
            # fx_rate is global market data, not household-scoped — leaving these rows behind
            # would shift the "most recent rate on or before" lookup other tests' fixtures rely
            # on, so they are removed explicitly rather than left for a later test to trip over.
            cur.execute(
                """DELETE FROM fx_rate WHERE base_currency = %s AND quote_currency = %s
                     AND rate_date = ANY(%s)""",
                (secondary, base, [date(2023, 12, 1), date(2024, 1, 15)]),
            )
            cur.execute(
                "DELETE FROM month_end_position WHERE household_id = ANY(%s)",
                ([household_id, other_household_id],),
            )
            cur.execute(
                "DELETE FROM wealth_history_refresh WHERE household_id = ANY(%s)",
                ([household_id, other_household_id],),
            )
            cur.execute(
                "DELETE FROM transaction WHERE household_id = ANY(%s)",
                ([household_id, other_household_id],),
            )
            clear_synthetic_account_valuation_periods(cur)
            both = [household_id, other_household_id]
            cur.execute("DELETE FROM app_user WHERE household_id = ANY(%s)", (both,))
            cur.execute("DELETE FROM account WHERE household_id = ANY(%s)", (both,))
            cur.execute("DELETE FROM person WHERE household_id = ANY(%s)", (both,))
            cur.execute("DELETE FROM household WHERE id = ANY(%s)", (both,))
        connection.commit()
        connection.close()


def _refresh() -> None:
    response = _client().post("/wealth-history/refresh", headers=ORIGIN)
    assert response.status_code == 200, response.text


def _attribution(month_end: str) -> dict[str, Any]:
    response = _client().get(f"/wealth-history/{month_end}/attribution", headers=ORIGIN)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_attribution_matches_the_pure_function_in_both_currencies(live: AttributionFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.owner
    try:
        _refresh()
        body = _attribution("2024-01-31")
    finally:
        app.dependency_overrides.clear()

    # The fixture's own facts, hand-built as the pure function's typed inputs: one ledger
    # position, present at both ends, with one reportable income flow and no other movement.
    position = attribution.Position(
        key=str(live.account_id),
        kind="ledger_balance",
        currency=live.base,
        opening_native=Decimal("1000.0000"),
        opening_value=Decimal("1000.0000"),
        closing_native=Decimal("1200.0000"),
        closing_value=Decimal("1200.0000"),
    )
    flow = attribution.Flow(
        position_key=str(live.account_id),
        currency=live.base,
        native_amount=Decimal("200.0000"),
        frozen_amount=Decimal("200.0000"),
        is_reportable=True,
        is_excluded=False,
        txn_type="income",
    )
    expected_base = attribution.attribute(
        positions=[position],
        flows=[flow],
        holding_funding=[],
        closing_rates={live.base: Decimal("1")},
    )

    secondary_position = attribution.Position(
        key=str(live.account_id),
        kind="ledger_balance",
        currency=live.base,
        opening_native=Decimal("1000.0000"),
        opening_value=Decimal("500.0000"),
        closing_native=Decimal("1200.0000"),
        closing_value=Decimal("600.0000"),
    )
    secondary_flow = attribution.Flow(
        position_key=str(live.account_id),
        currency=live.base,
        native_amount=Decimal("200.0000"),
        frozen_amount=Decimal("100.0000"),
        is_reportable=True,
        is_excluded=False,
        txn_type="income",
    )
    expected_secondary = attribution.attribute(
        positions=[secondary_position],
        flows=[secondary_flow],
        holding_funding=[],
        closing_rates={live.base: Decimal("0.5000000000")},
    )

    assert body["base"]["available"] is True
    assert body["base"]["opening"] == str(expected_base.opening)
    assert body["base"]["closing"] == str(expected_base.closing)
    assert body["base"]["actualChange"] == str(expected_base.actual_change)
    assert body["base"]["retainedIncome"] == str(expected_base.retained_income)
    assert body["base"]["residual"] == "0.0000" == str(expected_base.residual)

    assert body["secondary"]["opening"] == str(expected_secondary.opening)
    assert body["secondary"]["closing"] == str(expected_secondary.closing)
    assert body["secondary"]["actualChange"] == str(expected_secondary.actual_change)
    assert body["secondary"]["retainedIncome"] == str(expected_secondary.retained_income)
    assert body["secondary"]["residual"] == "0.0000" == str(expected_secondary.residual)


def test_foreign_household_gets_a_trivially_empty_attribution(live: AttributionFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        body = _attribution("2024-01-31")
    finally:
        app.dependency_overrides.clear()
    # No refresh, no accounts: vacuously complete, like GET /wealth-history for an empty month.
    assert body["base"]["available"] is True
    assert body["base"]["actualChange"] == "0.0000"
    assert body["base"]["residual"] == "0.0000"


def test_attribution_route_requires_owner_session() -> None:
    app.dependency_overrides.clear()
    assert _client().get("/wealth-history/2024-01-31/attribution").status_code == 401
