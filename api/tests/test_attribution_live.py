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
    other_account_id: UUID


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
        # The other household's own, distinct data — a different opening balance and no flows —
        # so a real isolation proof can assert its attribution is its own numbers, not the
        # owner household's, rather than two households that both happen to be empty (OL-002).
        other_account_id = _account(
            cur,
            other_household_id,
            other_person_id,
            "savings",
            base,
            opened_on=date(2023, 12, 1),
            opening_balance="500",
            opening_balance_date=date(2023, 12, 1),
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
            other_account_id=other_account_id,
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


# OL-001 (review repair round): a month this household has never computed, doesn't cover, or
# isn't a real calendar month end fails closed with one fixed reason, rather than silently
# treating an unrefreshed or out-of-range request as a vacuously empty, "available" month.

_NOT_COMPUTED_REASON = "month not computed; refresh wealth history"


def test_never_refreshed_household_is_unavailable(live: AttributionFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.owner
    try:
        body = _attribution("2024-01-31")
    finally:
        app.dependency_overrides.clear()
    assert body["base"]["available"] is False
    assert body["base"]["reasons"] == [_NOT_COMPUTED_REASON]
    assert body["base"]["actualChange"] is None
    assert body["secondary"]["available"] is False
    assert body["secondary"]["reasons"] == [_NOT_COMPUTED_REASON]


def test_non_month_end_date_is_unavailable_after_refresh(live: AttributionFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.owner
    try:
        _refresh()
        body = _attribution("2024-01-15")
    finally:
        app.dependency_overrides.clear()
    assert body["base"]["available"] is False
    assert body["base"]["reasons"] == [_NOT_COMPUTED_REASON]


def test_month_after_computed_through_is_unavailable(live: AttributionFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.owner
    try:
        _refresh()
        body = _attribution("2099-01-31")
    finally:
        app.dependency_overrides.clear()
    assert body["base"]["available"] is False
    assert body["base"]["reasons"] == [_NOT_COMPUTED_REASON]


def test_2024_01_31_stays_available_after_refresh(live: AttributionFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.owner
    try:
        _refresh()
        body = _attribution("2024-01-31")
    finally:
        app.dependency_overrides.clear()
    assert body["base"]["available"] is True
    assert body["secondary"]["available"] is True


def test_other_household_attribution_is_isolated_after_both_refresh(
    live: AttributionFixture,
) -> None:
    """A real isolation proof, not a vacuous one: both households refresh with their own, real
    data, and the other owner's attribution for the same calendar month is its own household's
    numbers — the owner household's opening 1000 / closing 1200 never appear (OL-002)."""
    app.dependency_overrides[auth.require_owner] = lambda: live.owner
    try:
        _refresh()
    finally:
        app.dependency_overrides.clear()

    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        _refresh()
        body = _attribution("2024-01-31")
    finally:
        app.dependency_overrides.clear()

    assert body["base"]["available"] is True
    assert body["base"]["opening"] == "500.0000"
    assert body["base"]["closing"] == "500.0000"
    assert body["base"]["actualChange"] == "0.0000"
    assert body["base"]["residual"] == "0.0000"
    assert body["base"]["opening"] != "1000.0000"
    assert body["base"]["closing"] != "1200.0000"
    assert body["base"]["retainedIncome"] != "200.0000"


def test_attribution_route_requires_owner_session() -> None:
    app.dependency_overrides.clear()
    assert _client().get("/wealth-history/2024-01-31/attribution").status_code == 401


# OL-003 (review repair round): a second, standalone live scenario — not the shared `live`
# fixture, so it cannot disturb the other tests' hand-verified numbers — that exercises the
# route's own SQL filters directly: a corrected (superseded) purchase excluded from funding, a
# cash_distribution event that must never load as funding (it moves cash but not units, and is
# not one of the spec's funding kinds), a non-reportable ledger transfer, and a posting booked
# exactly on t0 that belongs to the opening balance, never the window's flows.


def _correct_purchase(
    original_id: str, security_id: UUID, account_id: UUID, currency: str, units: str, price: str
) -> None:
    gross = str((Decimal(units) * Decimal(price)).quantize(Decimal("0.0001")))
    response = _client().post(
        f"/investment-events/{original_id}/corrections",
        json={
            "accountId": str(account_id),
            "securityId": str(security_id),
            "eventDate": "2024-01-05",
            "eventKind": "purchase",
            "units": units,
            "price": price,
            "grossAmount": gross,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": f"-{gross}",
            "currency": currency,
            "idempotencyKey": str(uuid4()),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic OL-003 correction",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _cash_distribution(security_id: UUID, account_id: UUID, currency: str) -> None:
    response = _client().post(
        "/investment-events/cash-lifecycle",
        json={
            "accountId": str(account_id),
            "securityId": str(security_id),
            "eventDate": "2024-01-20",
            "eventKind": "cash_distribution",
            "units": None,
            "price": None,
            "grossAmount": "5.0000",
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": "5.0000",
            "currency": currency,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def test_corrected_purchase_distribution_and_t0_posting_are_excluded_from_the_window() -> None:
    # Standalone: connect_to_test_database() refuses a second connection while the shared `live`
    # fixture's own rows are still committed, so this scenario opens (and cleans up) its own
    # household entirely, like test_wealth_history_live's own
    # test_31_december_2023_is_listed_even_with_no_account_open_that_day.
    connection = connect_to_test_database()
    household_id, person_id, user_id = uuid4(), uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "ol003-owner@example.invalid")
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = str(found[0]).strip(), str(found[1]).strip()
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
               VALUES (%s, 'OL-003 household', %s, %s)""",
            (household_id, base, secondary),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, 'OL-003 owner', true)""",
            (person_id, household_id),
        )
        cur.execute(
            """INSERT INTO app_user (id, household_id, person_id, email, role)
               VALUES (%s, %s, %s, %s, 'owner')""",
            (user_id, household_id, person_id, owner.email),
        )
        for on in (date(2023, 12, 31), date(2024, 1, 5), date(2024, 1, 10), date(2024, 1, 20)):
            _secondary_to_base_rate(cur, on, secondary, base, "2.0000000000")

        ledger_id = _account(
            cur,
            household_id,
            person_id,
            "savings",
            base,
            opened_on=date(2023, 12, 1),
            opening_balance="1000",
            opening_balance_date=date(2023, 12, 1),
        )
        holdings_id = _account(
            cur,
            household_id,
            person_id,
            "brokerage",
            base,
            opened_on=date(2023, 12, 1),
            opening_balance="0",
            opening_balance_date=date(2023, 12, 1),
        )
        cur.execute(
            """INSERT INTO security (household_id, security_type, name, currency, country,
                                      asset_class)
               VALUES (%s, 'equity', 'Synthetic OL-003 security', %s, 'XZ', 'equity')
               RETURNING id""",
            (household_id, base),
        )
        security_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]

        # On t0 itself (2023-12-31): counted in the opening balance, never the window's flows.
        post_ledger_transaction(
            cur,
            household_id,
            ledger_id,
            date(2023, 12, 31),
            "Synthetic t0 credit",
            Decimal("50"),
            "income",
            None,
        )
    connection.commit()

    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        with connection.cursor() as cur:
            post_ledger_transaction(
                cur,
                household_id,
                ledger_id,
                date(2024, 1, 10),
                "Synthetic non-reportable transfer",
                Decimal("-80"),
                "transfer_out",
                None,
            )
        connection.commit()

        original = _client().post(
            "/investment-events/acquisitions",
            json={
                "accountId": str(holdings_id),
                "securityId": str(security_id),
                "eventDate": "2024-01-05",
                "eventKind": "purchase",
                "units": "10.00000000",
                "price": "2.000000",
                "grossAmount": "20.0000",
                "charges": "0.0000",
                "withheldTax": "0.0000",
                "netAmount": "-20.0000",
                "currency": base,
                "idempotencyKey": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert original.status_code == 201, original.text
        _correct_purchase(
            original.json()["id"], security_id, holdings_id, base, "8.00000000", "2.000000"
        )
        _cash_distribution(security_id, holdings_id, base)

        response = _client().post(
            f"/investment-catalogue/securities/{security_id}/manual-prices",
            json={"priceDate": "2024-01-31", "price": "2.500000"},
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text

        _refresh()
        body = _attribution("2024-01-31")
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute(
                """DELETE FROM fx_rate WHERE base_currency = %s AND quote_currency = %s
                     AND rate_date = ANY(%s)""",
                (
                    secondary,
                    base,
                    [date(2023, 12, 31), date(2024, 1, 5), date(2024, 1, 10), date(2024, 1, 20)],
                ),
            )
            cur.execute("DELETE FROM month_end_position WHERE household_id = %s", (household_id,))
            cur.execute(
                "DELETE FROM wealth_history_refresh WHERE household_id = %s", (household_id,)
            )
            cur.execute("DELETE FROM transaction WHERE household_id = %s", (household_id,))
            clear_synthetic_account_valuation_periods(cur)
            # Investment tables restrict deletion by design (preserved history), like
            # test_wealth_history_live's own `live` fixture: account/person/household stay
            # behind for this run's own throwaway database rather than fighting
            # fk_holding_account_household.
        connection.commit()
        connection.close()

    assert body["base"]["available"] is True
    assert body["base"]["opening"] == "1050.0000"  # 1000 + the t0-dated credit
    assert body["base"]["closing"] == "990.0000"  # 970 (ledger) + 20 (holding, 8 units @ 2.5)
    assert body["base"]["actualChange"] == "-60.0000"
    assert body["base"]["retainedIncome"] == "0.0000"
    # -80 (ledger transfer) + 16 (the correction's own -16 funding, negated): the corrected-away
    # original purchase (-20) and the cash_distribution's +5 never appear here.
    assert body["base"]["internalMovementsAndCosts"] == "-64.0000"
    assert body["base"]["marketMovement"] == "4.0000"  # (20 - 0 - 16) at the closing rate
    assert body["base"]["otherExternalChanges"] == "0.0000"
    assert body["base"]["residual"] == "0.0000"

    ledger_position = attribution.Position(
        key=str(ledger_id),
        kind="ledger_balance",
        currency=base,
        opening_native=Decimal("1050.0000"),
        opening_value=Decimal("1050.0000"),
        closing_native=Decimal("970.0000"),
        closing_value=Decimal("970.0000"),
    )
    # The pure function only needs a key shared between a position and its own flows/funding —
    # not the route's own "accountId:holdingId" spelling, since the holding's real id was
    # assigned by the database and is not independently reconstructed here.
    holding_position = attribution.Position(
        key="holding",
        kind="holdings",
        currency=base,
        opening_native=Decimal("0.0000"),
        opening_value=Decimal("0.0000"),
        closing_native=Decimal("20.0000"),
        closing_value=Decimal("20.0000"),
    )
    transfer_flow = attribution.Flow(
        position_key=str(ledger_id),
        currency=base,
        native_amount=Decimal("-80.0000"),
        frozen_amount=Decimal("-80.0000"),
        is_reportable=False,
        is_excluded=False,
        txn_type="transfer_out",
    )
    funding = attribution.HoldingFunding(
        position_key="holding",
        currency=base,
        native_net=Decimal("-16.0000"),
        frozen_net=Decimal("-16.0000"),
    )
    expected = attribution.attribute(
        positions=[ledger_position, holding_position],
        flows=[transfer_flow],
        holding_funding=[funding],
        closing_rates={base: Decimal("1")},
    )
    assert body["base"]["actualChange"] == str(expected.actual_change)
    assert body["base"]["internalMovementsAndCosts"] == str(expected.internal_movements_and_costs)
    assert body["base"]["marketMovement"] == str(expected.market_movement)
    assert body["base"]["residual"] == "0.0000" == str(expected.residual)
