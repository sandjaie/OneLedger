"""Phase 2.2 Task 1: `POST /wealth-history/refresh`, `GET /wealth-history` and
`GET /wealth-history/{monthEnd}` (spec "Month-end positions" and "API").

One synthetic household exercises every stored-position status: a ledger account posted on its
own opening date and again later (`assert_account_reconciles`'s own figure), an account closed
mid-history that keeps the months it was open, an account opened after the anchor
(`before_opening`), a holdings-mode account with a priced holding (later fully sold) and an
unpriced one (`missing_price`), a snapshot-mode account with a carried snapshot translated at
each month's own rate, a snapshot-mode account with no snapshot ever (`no_snapshot`), and a
ledger account in a currency no `fx_rate` row ever covers (`missing_rate`). A second, otherwise
empty household proves isolation. Skipped when no database is reachable. Every name, security and
amount is synthetic.
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

from oneledger import auth, wealth_history
from oneledger.db import scope_to_owner
from oneledger.main import app
from oneledger.routes.transactions import post_ledger_transaction

from .live_database import clear_synthetic_account_valuation_periods, connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class WealthFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    base: str
    secondary: str
    third: str
    ledger_id: UUID
    closed_id: UUID
    before_id: UUID
    no_rate_id: UUID
    holdings_id: UUID
    snapshot_id: UUID
    no_snapshot_id: UUID
    priced_security_id: UUID
    unpriced_security_id: UUID


def _client() -> TestClient:
    return TestClient(app)


def _account(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    person_id: UUID,
    account_type: str,
    currency: str,
    *,
    opened_on: date | None,
    opening_balance: str,
    opening_balance_date: date,
    closed_on: date | None = None,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opened_on, closed_on, opening_balance,
                                 opening_balance_date, is_active)
           VALUES (%s, %s, 'Synthetic wealth history account', %s::account_type, %s, 'XZ', %s,
                   %s, %s, %s, %s) RETURNING id""",
        (
            household_id,
            person_id,
            account_type,
            currency,
            opened_on,
            closed_on,
            Decimal(opening_balance),
            opening_balance_date,
            closed_on is None,
        ),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


def _secondary_to_base_rate(
    cur: psycopg.Cursor[Any], on: date, secondary: str, base: str, rate: str
) -> None:
    # A row means "1 base_currency = rate quote_currency" (`current_rate_pair` / `money._frozen`'s
    # convention); every rate this fixture needs is quoted secondary-to-base.
    cur.execute(
        """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
           VALUES (%s, %s, %s, %s, 'manual')
           ON CONFLICT (rate_date, base_currency, quote_currency)
           DO UPDATE SET rate = EXCLUDED.rate""",
        (on, secondary, base, Decimal(rate)),
    )


def _acquire(
    security_id: UUID, account_id: UUID, currency: str, event_date: str, units: str, price: str
) -> None:
    gross = str((Decimal(units) * Decimal(price)).quantize(Decimal("0.0001")))
    response = _client().post(
        "/investment-events/acquisitions",
        json={
            "accountId": str(account_id),
            "securityId": str(security_id),
            "eventDate": event_date,
            "eventKind": "purchase",
            "units": units,
            "price": price,
            "grossAmount": gross,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": f"-{gross}",
            "currency": currency,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _price(security_id: UUID, on: str, price: str) -> None:
    response = _client().post(
        f"/investment-catalogue/securities/{security_id}/manual-prices",
        json={"priceDate": on, "price": price},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _sell(
    security_id: UUID, account_id: UUID, currency: str, event_date: str, units: str, price: str
) -> None:
    gross = str((-Decimal(units) * Decimal(price)).quantize(Decimal("0.0001")))
    response = _client().post(
        "/investment-events/cash-lifecycle",
        json={
            "accountId": str(account_id),
            "securityId": str(security_id),
            "eventDate": event_date,
            "eventKind": "sale",
            "units": units,
            "price": price,
            "grossAmount": gross,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": gross,
            "currency": currency,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


@pytest.fixture
def live() -> Iterator[WealthFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "wealth-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-wealth-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = str(found[0]).strip(), str(found[1]).strip()
        cur.execute(
            "SELECT code FROM currency WHERE code NOT IN (%s, %s) ORDER BY code LIMIT 1",
            (base, secondary),
        )
        third_row = cur.fetchone()
        assert third_row is not None
        third = str(third_row[0]).strip()

        for household, name in (
            (household_id, "wealth history household"),
            (other_household_id, "other wealth history household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "wealth history owner"),
            (other_person_id, other_household_id, "other wealth history owner"),
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

        # Secondary-to-base rate: 2.0 through January, 2.5 from 1 February onward — a change
        # between months so a snapshot's carried native value still translates differently.
        for on, rate in (
            (date(2024, 1, 1), "2.0000000000"),
            (date(2024, 1, 10), "2.0000000000"),
            (date(2024, 2, 1), "2.5000000000"),
            (date(2024, 2, 15), "2.5000000000"),
            (date(2024, 3, 10), "2.5000000000"),
        ):
            _secondary_to_base_rate(cur, on, secondary, base, rate)

        ledger_id = _account(
            cur,
            household_id,
            person_id,
            "savings",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance="1000",
            opening_balance_date=date(2024, 1, 1),
        )
        closed_id = _account(
            cur,
            household_id,
            person_id,
            "savings",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance="300",
            opening_balance_date=date(2024, 1, 1),
            closed_on=date(2024, 2, 29),
        )
        # Never opened (so it is still counted every month by the lifetime predicate), but its
        # ledger balance is not anchored until March — exercising `before_opening`, not exclusion.
        before_id = _account(
            cur,
            household_id,
            person_id,
            "savings",
            base,
            opened_on=None,
            opening_balance="50",
            opening_balance_date=date(2024, 3, 1),
        )
        no_rate_id = _account(
            cur,
            household_id,
            person_id,
            "savings",
            third,
            opened_on=date(2024, 1, 1),
            opening_balance="100",
            opening_balance_date=date(2024, 1, 1),
        )
        holdings_id = _account(
            cur,
            household_id,
            person_id,
            "brokerage",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance="0",
            opening_balance_date=date(2024, 1, 1),
        )
        snapshot_id = _account(
            cur,
            household_id,
            person_id,
            "property",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance="0",
            opening_balance_date=date(2024, 1, 1),
        )
        no_snapshot_id = _account(
            cur,
            household_id,
            person_id,
            "property",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance="0",
            opening_balance_date=date(2024, 1, 1),
        )

        cur.execute(
            """INSERT INTO security (
                   household_id, security_type, name, currency, country, asset_class)
               VALUES (%s, 'equity', 'Synthetic priced security', %s, 'XZ', 'equity')
               RETURNING id""",
            (household_id, base),
        )
        priced_security_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
        cur.execute(
            """INSERT INTO security (
                   household_id, security_type, name, currency, country, asset_class)
               VALUES (%s, 'equity', 'Synthetic unpriced security', %s, 'XZ', 'equity')
               RETURNING id""",
            (household_id, base),
        )
        unpriced_security_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]

        cur.execute(
            """INSERT INTO valuation_snapshot (
                   household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
                   fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary, method,
                   confidence)
               VALUES (%s, %s, '2024-01-15', 5000, %s, '2024-01-15', 1, 1, 5000, 5000,
                       'owner_entered', 'high')""",
            (household_id, snapshot_id, base),
        )

        # Ledger postings: one on the account's own opening date (proving
        # `assert_account_reconciles` counts it), one later in the same month's neighbour.
        post_ledger_transaction(
            cur,
            household_id,
            ledger_id,
            date(2024, 1, 1),
            "Synthetic opening-day credit",
            Decimal("500"),
            "income",
            None,
        )
        post_ledger_transaction(
            cur,
            household_id,
            ledger_id,
            date(2024, 2, 15),
            "Synthetic later credit",
            Decimal("200"),
            "income",
            None,
        )
    connection.commit()

    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        _acquire(priced_security_id, holdings_id, base, "2024-01-10", "10", "10.000000")
        _acquire(unpriced_security_id, holdings_id, base, "2024-01-10", "5", "1.000000")
        _price(priced_security_id, "2024-01-31", "12.000000")
        _price(priced_security_id, "2024-02-29", "15.000000")
        _sell(priced_security_id, holdings_id, base, "2024-03-10", "-10", "16.000000")

        yield WealthFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            base=base,
            secondary=secondary,
            third=third,
            ledger_id=ledger_id,
            closed_id=closed_id,
            before_id=before_id,
            no_rate_id=no_rate_id,
            holdings_id=holdings_id,
            snapshot_id=snapshot_id,
            no_snapshot_id=no_snapshot_id,
            priced_security_id=priced_security_id,
            unpriced_security_id=unpriced_security_id,
        )
    finally:
        app.dependency_overrides.clear()
        # Investment tables restrict deletion by design (preserved history); this fixture, like
        # `test_investment_events_live.py`'s `live_events`, leaves its holding/security/investment
        # rows behind for the run's own throwaway database (`live_database.py`) and only clears
        # what later tests in the same process could otherwise trip over.
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM month_end_position WHERE household_id = ANY(%s)",
                ([household_id, other_household_id],),
            )
            cur.execute(
                "DELETE FROM transaction WHERE household_id = ANY(%s)",
                ([household_id, other_household_id],),
            )
            clear_synthetic_account_valuation_periods(cur)
        connection.commit()
        connection.close()


def _refresh() -> dict[str, Any]:
    response = _client().post("/wealth-history/refresh", headers=ORIGIN)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _month(monthEnd: str) -> dict[str, Any]:
    response = _client().get(f"/wealth-history/{monthEnd}", headers=ORIGIN)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _position(
    body: dict[str, Any], *, account_id: UUID, holding_id: UUID | None = None
) -> dict[str, Any]:
    key = str(account_id)
    hkey = None if holding_id is None else str(holding_id)
    return next(p for p in body["positions"] if p["accountId"] == key and p["holdingId"] == hkey)


def test_ledger_month_end_balance_matches_reconciliation(live: WealthFixture) -> None:
    _refresh()

    january = _month("2024-01-31")
    row = _position(january, account_id=live.ledger_id)
    assert row["nativeValue"] == "1500.0000"  # opening 1000 + the opening-day credit of 500
    assert row["evidenceDate"] == "2024-01-01"
    assert row["status"] == "available"
    assert row["valueBase"] == "1500.0000"
    assert row["valueSecondary"] == "750.0000"  # rate to secondary is 1/2.0 in January

    february = _month("2024-02-29")
    row2 = _position(february, account_id=live.ledger_id)
    assert row2["nativeValue"] == "1700.0000"  # +200 more on the 15th
    assert row2["evidenceDate"] == "2024-02-15"
    assert row2["valueSecondary"] == "680.0000"  # rate to secondary is 1/2.5 in February

    scope_to_owner(live.connection, live.owner.user_id)
    with live.connection.cursor() as cur:
        # Raises on mismatch: this is the same figure V14's assert_account_reconciles verifies,
        # computed independently by the fixture above.
        cur.execute(
            "SELECT assert_account_reconciles(%s, %s, %s)",
            (live.ledger_id, Decimal("1500.0000"), date(2024, 1, 31)),
        )
        cur.execute(
            "SELECT assert_account_reconciles(%s, %s, %s)",
            (live.ledger_id, Decimal("1700.0000"), date(2024, 2, 29)),
        )
    live.connection.rollback()


def test_before_opening_month_is_incomplete(live: WealthFixture) -> None:
    result = _refresh()
    assert result["byStatus"].get("before_opening", 0) >= 2

    january = _month("2024-01-31")
    row = _position(january, account_id=live.before_id)
    assert row["status"] == "before_opening"
    assert row["nativeValue"] is None
    assert row["valueBase"] is None

    history = _client().get("/wealth-history", headers=ORIGIN).json()
    by_month = {m["monthEnd"]: m for m in history["months"]}
    assert by_month["2024-01-31"]["complete"] is False


def test_closed_account_keeps_history_through_its_closing_month(live: WealthFixture) -> None:
    _refresh()

    january = _month("2024-01-31")
    assert _position(january, account_id=live.closed_id)["nativeValue"] == "300.0000"

    february = _month("2024-02-29")
    assert _position(february, account_id=live.closed_id)["nativeValue"] == "300.0000"

    march = _month("2024-03-31")
    with pytest.raises(StopIteration):
        _position(march, account_id=live.closed_id)


def test_holding_valued_at_month_end_price_and_fully_sold_is_zero(live: WealthFixture) -> None:
    _refresh()

    january = _month("2024-01-31")
    # Holding rows carry their own holdingId, never null, so look them up by native value
    # instead: locate the one priced at 12.0 for ten units.
    priced_row = next(
        p
        for p in january["positions"]
        if p["accountId"] == str(live.holdings_id) and p["nativeValue"] == "120.0000"
    )
    assert priced_row["evidenceDate"] == "2024-01-31"
    assert priced_row["valuationMode"] == "holdings"
    assert priced_row["status"] == "available"

    february = _month("2024-02-29")
    priced_feb = next(
        p
        for p in february["positions"]
        if p["accountId"] == str(live.holdings_id) and p["nativeValue"] == "150.0000"
    )
    assert priced_feb["evidenceDate"] == "2024-02-29"

    march = _month("2024-03-31")
    sold_row = next(
        p
        for p in march["positions"]
        if p["accountId"] == str(live.holdings_id) and p["holdingId"] == priced_row["holdingId"]
    )
    assert sold_row["nativeValue"] == "0.0000"
    assert sold_row["status"] == "available"
    assert sold_row["valueBase"] == "0.0000"


def test_missing_price_status_for_unpriced_holding(live: WealthFixture) -> None:
    _refresh()
    january = _month("2024-01-31")
    unpriced_rows = [
        p
        for p in january["positions"]
        if p["accountId"] == str(live.holdings_id) and p["status"] == "missing_price"
    ]
    assert len(unpriced_rows) == 1
    assert unpriced_rows[0]["nativeValue"] is None
    assert unpriced_rows[0]["valueBase"] is None


def test_snapshot_carry_forward_translated_at_month_end_rate(live: WealthFixture) -> None:
    _refresh()

    january = _month("2024-01-31")
    row = _position(january, account_id=live.snapshot_id)
    assert row["nativeValue"] == "5000.0000"
    assert row["evidenceDate"] == "2024-01-15"
    assert row["valueBase"] == "5000.0000"
    assert row["valueSecondary"] == "2500.0000"  # 5000 / 2.0

    february = _month("2024-02-29")
    row2 = _position(february, account_id=live.snapshot_id)
    assert row2["nativeValue"] == "5000.0000"  # the same snapshot carries forward flat
    assert row2["evidenceDate"] == "2024-01-15"
    assert row2["valueSecondary"] == "2000.0000"  # 5000 / 2.5 — February's own rate, not January's


def test_no_snapshot_status(live: WealthFixture) -> None:
    _refresh()
    january = _month("2024-01-31")
    row = _position(january, account_id=live.no_snapshot_id)
    assert row["status"] == "no_snapshot"
    assert row["nativeValue"] is None


def test_missing_rate_status_for_uncovered_currency(live: WealthFixture) -> None:
    _refresh()
    january = _month("2024-01-31")
    row = _position(january, account_id=live.no_rate_id)
    assert row["status"] == "missing_rate"
    assert row["nativeValue"] == "100.0000"  # known, even though it cannot be translated
    assert row["valueBase"] is None
    assert row["valueSecondary"] is None
    assert row["rateToBase"] is None


def test_refresh_is_idempotent(live: WealthFixture) -> None:
    _refresh()
    first = _month("2024-01-31")
    _refresh()
    second = _month("2024-01-31")

    def sorted_positions(body: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted(body["positions"], key=lambda p: (p["accountId"], str(p.get("holdingId"))))

    assert sorted_positions(first) == sorted_positions(second)


def test_late_refresh_failure_keeps_previous_rows(
    live: WealthFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refresh()
    before = _month("2024-01-31")

    calls = {"n": 0}
    original = wealth_history.read_positions

    def boom(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("synthetic mid-refresh failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(wealth_history, "read_positions", boom)
    # TestClient re-raises an in-app exception rather than returning it as a 500 response; either
    # way nothing commits, because `refresh` never deletes-and-reinserts outside one transaction.
    with pytest.raises(RuntimeError, match="synthetic mid-refresh failure"):
        _client().post("/wealth-history/refresh", headers=ORIGIN)

    monkeypatch.undo()
    after = _month("2024-01-31")
    assert after == before


def test_foreign_household_sees_no_rows_without_its_own_refresh(live: WealthFixture) -> None:
    _refresh()
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        history = _client().get("/wealth-history", headers=ORIGIN).json()
        assert history["months"] == []
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live.owner


def test_get_never_recomputes(live: WealthFixture) -> None:
    empty = _client().get("/wealth-history", headers=ORIGIN).json()
    assert empty["months"] == []

    _refresh()
    first = _client().get("/wealth-history", headers=ORIGIN).json()
    second = _client().get("/wealth-history", headers=ORIGIN).json()
    assert first == second


def test_routes_require_owner_session() -> None:
    app.dependency_overrides.clear()
    assert _client().post("/wealth-history/refresh").status_code == 401
    assert _client().get("/wealth-history").status_code == 401
    assert _client().get("/wealth-history/2024-01-31").status_code == 401
