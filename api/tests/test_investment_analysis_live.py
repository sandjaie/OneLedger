"""Phase 4.3 Task 1: `GET /portfolio/analysis` and the allocation-target and benchmark
correction routes against the live database (spec
`docs/superpowers/specs/2026-09-16-phase-4-3-investment-analysis.md`).

One synthetic household gets two brokerage holdings accounts (`account_id`, `other_account_id`),
an equity and a fixed-income security, and a retirement (snapshot-mode) account for the coverage
list. Each test posts exactly the acquisitions, prices, switches, transfers and wealth-history
refresh it needs through the real HTTP routes -- `investment-events`, `investment-catalogue` and
`wealth-history` -- so the analysis route is exercised against data the same write paths a real
household would use produced, not a hand-inserted shortcut. A second, otherwise empty household
proves isolation. Skipped when no database is reachable. Every name, security and amount is
synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.investments import cash_lifecycle, commands, unit_actions
from oneledger.investments.returns import CashFlow, xirr
from oneledger.main import app
from oneledger.routes import investment_catalogue, portfolio, wealth_history

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class AnalysisFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    account_id: UUID
    other_account_id: UUID
    retirement_account_id: UUID
    equity_security_id: UUID
    bond_security_id: UUID
    other_security_id: UUID
    currency: str
    secondary_currency: str


def _account(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    person_id: UUID,
    account_type: str,
    currency: str,
    name: str,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opened_on, opening_balance, opening_balance_date)
           VALUES (%s, %s, %s, %s::account_type, %s, 'XZ', '2024-01-01', 0, '2024-01-01')
           RETURNING id""",
        (household_id, person_id, name, account_type, currency),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _security(
    cur: psycopg.Cursor[Any], household_id: UUID, asset_class: str, currency: str, name: str
) -> UUID:
    cur.execute(
        """INSERT INTO security (household_id, security_type, name, currency, country,
                                  asset_class)
           VALUES (%s, 'mutual_fund', %s, %s, 'XZ', %s::asset_class) RETURNING id""",
        (household_id, name, currency, asset_class),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


@pytest.fixture
def analysis_live(monkeypatch: pytest.MonkeyPatch) -> Iterator[AnalysisFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(
        user_id=user_id,
        household_id=household_id,
        session_id=uuid4(),
        email="analysis-owner@example.invalid",
    )
    other_owner = auth.OwnerSession(
        user_id=other_user_id,
        household_id=other_household_id,
        session_id=uuid4(),
        email="other-analysis-owner@example.invalid",
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = currencies
        base, secondary = str(base).strip(), str(secondary).strip()
        for household, name in (
            (household_id, "analysis household"),
            (other_household_id, "other analysis household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        # `money._frozen` (an acquisition, switch or platform-transfer's own frozen rate) needs
        # an exact row for its own event date; `current_rate_pair` (as-of valuation) carries the
        # earliest of these forward for every later date, matching
        # `test_investment_events_live.py`'s own two exact rows.
        for on in ("2024-01-01", "2024-01-10", "2024-01-15"):
            cur.execute(
                """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
                   VALUES (%s, %s, %s, 0.5000000000, 'manual')
                   ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                     SET rate = EXCLUDED.rate""",
                (on, secondary, base),
            )
        for person, household, name in (
            (person_id, household_id, "analysis owner"),
            (other_person_id, other_household_id, "other analysis owner"),
        ):
            cur.execute(
                """INSERT INTO person (household_id, id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (household, person, name),
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
        account_id = _account(
            cur, household_id, person_id, "brokerage", base, "Synthetic brokerage account"
        )
        other_account_id = _account(
            cur, household_id, person_id, "brokerage", base, "Other synthetic brokerage account"
        )
        retirement_account_id = _account(
            cur, household_id, person_id, "retirement", base, "Synthetic retirement account"
        )
        equity_security_id = _security(cur, household_id, "equity", base, "Synthetic equity fund")
        bond_security_id = _security(cur, household_id, "fixed_income", base, "Synthetic bond fund")
        other_security_id = _security(
            cur, other_household_id, "equity", base, "Other synthetic equity fund"
        )
        cur.execute(
            """INSERT INTO valuation_snapshot (
                   household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
                   fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary, method,
                   confidence)
               VALUES (%s, %s, '2024-01-15', 5000, %s, '2024-01-15', 1, 1, 5000, 5000,
                       'owner_entered', 'high')""",
            (household_id, retirement_account_id, base),
        )
    connection.commit()

    monkeypatch.setattr(commands, "connect", connect_to_test_database)
    monkeypatch.setattr(cash_lifecycle, "connect", connect_to_test_database)
    monkeypatch.setattr(unit_actions, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    monkeypatch.setattr(wealth_history, "connect", connect_to_test_database)
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)

    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield AnalysisFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            account_id=account_id,
            other_account_id=other_account_id,
            retirement_account_id=retirement_account_id,
            equity_security_id=equity_security_id,
            bond_security_id=bond_security_id,
            other_security_id=other_security_id,
            currency=base,
            secondary_currency=secondary,
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _client() -> TestClient:
    return TestClient(app)


def _acquire(
    fixture: AnalysisFixture,
    account_id: UUID,
    security_id: UUID,
    on: str,
    units: str,
    price: str,
) -> dict[str, Any]:
    gross = str((Decimal(units) * Decimal(price)).quantize(Decimal("0.0001")))
    response = _client().post(
        "/investment-events/acquisitions",
        json={
            "accountId": str(account_id),
            "securityId": str(security_id),
            "eventDate": on,
            "eventKind": "purchase",
            "units": units,
            "price": price,
            "grossAmount": gross,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": f"-{gross}",
            "currency": fixture.currency,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def _price(security_id: UUID, on: str, value: str) -> None:
    response = _client().post(
        f"/investment-catalogue/securities/{security_id}/manual-prices",
        json={"priceDate": on, "price": value},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _switch(
    fixture: AnalysisFixture,
    account_id: UUID,
    out_security_id: UUID,
    in_security_id: UUID,
    on: str,
    out_units: str,
    out_price: str,
    in_units: str,
    in_price: str,
) -> None:
    gross = str((Decimal(out_units) * Decimal(out_price)).quantize(Decimal("0.0001")))
    in_gross = str((Decimal(in_units) * Decimal(in_price)).quantize(Decimal("0.0001")))
    assert gross == in_gross, "the fixture's own switch legs must carry equal gross value"
    response = _client().post(
        "/investment-events/unit-actions",
        json={
            "kind": "switch",
            "accountId": str(account_id),
            "outSecurityId": str(out_security_id),
            "inSecurityId": str(in_security_id),
            "eventDate": on,
            "outUnits": f"-{out_units}",
            "outPrice": out_price,
            "outGrossAmount": gross,
            "inUnits": in_units,
            "inPrice": in_price,
            "inGrossAmount": in_gross,
            "currency": fixture.currency,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _platform_transfer(
    fixture: AnalysisFixture,
    out_account_id: UUID,
    in_account_id: UUID,
    security_id: UUID,
    on: str,
    units: str,
) -> None:
    response = _client().post(
        "/investment-events/unit-actions",
        json={
            "kind": "platform_transfer",
            "outAccountId": str(out_account_id),
            "inAccountId": str(in_account_id),
            "securityId": str(security_id),
            "eventDate": on,
            "outUnits": f"-{units}",
            "inUnits": units,
            "currency": fixture.currency,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _refresh() -> None:
    # The route always refreshes through `date.today()`'s own last completed month; there is no
    # `through` override.
    response = _client().post("/wealth-history/refresh", headers=ORIGIN)
    assert response.status_code in (200, 201), response.text


def _analysis(as_of: str) -> dict[str, Any]:
    response = _client().get("/portfolio/analysis", params={"asOf": as_of})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _class(payload: dict[str, Any], label: str) -> dict[str, Any]:
    [row] = [c for c in payload["classes"] if c["label"] == label]
    result: dict[str, Any] = row
    return result


def _account_row(payload: dict[str, Any], account_id: UUID) -> dict[str, Any]:
    [row] = [a for a in payload["accounts"] if a["accountId"] == str(account_id)]
    result: dict[str, Any] = row
    return result


def _member() -> auth.OwnerSession:
    raise HTTPException(status_code=403, detail="not authorized")


def _expected_dietz_percent(
    flows: list[tuple[str, Decimal]], t0: date, t1: date, bmv: Decimal, emv: Decimal
) -> Decimal:
    """Spec "Period return (Modified Dietz)" formula, hand-derived here independently of
    `investment_analysis.modified_dietz` so this file proves the route wires real data into the
    formula rather than re-checking the formula itself (already covered by
    `test_investment_analysis.py`)."""
    span = (t1 - t0).days
    with localcontext() as ctx:
        ctx.prec = 50
        total_c = Decimal(0)
        weighted = Decimal(0)
        for on, net_amount_base in flows:
            flow_date = date.fromisoformat(on)
            c = -net_amount_base
            total_c += c
            weight = Decimal((t1 - flow_date).days) / Decimal(span) if span else Decimal(0)
            weighted += c * weight
        denominator = bmv + weighted
        return ((emv - bmv - total_c) / denominator * 100).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_EVEN
        )


def test_analysis_requires_an_owner_session() -> None:
    response = _client().get("/portfolio/analysis", params={"asOf": "2024-01-31"})

    assert response.status_code == 401


def test_a_malformed_as_of_is_refused_with_400(analysis_live: AnalysisFixture) -> None:
    response = _client().get("/portfolio/analysis", params={"asOf": "not-a-date"})

    assert response.status_code == 400


def test_class_and_account_xirr_match_the_returns_engine_for_the_same_flows(
    analysis_live: AnalysisFixture,
) -> None:
    """Two holdings of different asset classes in one account: the class split must not merge
    their flows, and the account group must combine them exactly as the portfolio-level XIRR
    engine (`investments.returns.xirr`) would for the same combined flows."""
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        "2024-01-10",
        "4.00000000",
        "5.000000",
    )
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.bond_security_id,
        "2024-01-10",
        "3.00000000",
        "10.000000",
    )
    _price(analysis_live.equity_security_id, "2024-01-31", "6.000000")
    _price(analysis_live.bond_security_id, "2024-01-31", "11.000000")
    as_of = "2024-01-31"

    payload = _analysis(as_of)

    equity = _class(payload, "equity")
    bond = _class(payload, "fixed_income")
    account = _account_row(payload, analysis_live.account_id)
    assert equity["valueBase"] == "24.0000"
    assert bond["valueBase"] == "33.0000"
    assert account["valueBase"] == "57.0000"

    expected_equity = xirr(
        [
            CashFlow(date(2024, 1, 10), Decimal("-20.0000")),
            CashFlow(date(2024, 1, 31), Decimal("24.0000")),
        ]
    )
    expected_bond = xirr(
        [
            CashFlow(date(2024, 1, 10), Decimal("-30.0000")),
            CashFlow(date(2024, 1, 31), Decimal("33.0000")),
        ]
    )
    expected_account = xirr(
        [
            CashFlow(date(2024, 1, 10), Decimal("-50.0000")),
            CashFlow(date(2024, 1, 31), Decimal("57.0000")),
        ]
    )
    assert equity["xirrPercent"] == str(expected_equity.rate_percent)
    assert equity["xirrReason"] == expected_equity.reason
    assert bond["xirrPercent"] == str(expected_bond.rate_percent)
    assert bond["xirrReason"] == expected_bond.reason
    assert account["xirrPercent"] == str(expected_account.rate_percent)
    assert account["xirrReason"] == expected_account.reason
    assert payload["inception"]["dietzReason"] is None


def test_a_missing_price_fails_closed_only_for_its_own_class_and_account(
    analysis_live: AnalysisFixture,
) -> None:
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        "2024-01-10",
        "4.00000000",
        "5.000000",
    )
    _acquire(
        analysis_live,
        analysis_live.other_account_id,
        analysis_live.bond_security_id,
        "2024-01-10",
        "3.00000000",
        "10.000000",
    )
    _price(analysis_live.equity_security_id, "2024-01-31", "6.000000")
    # The bond security is never priced.
    as_of = "2024-01-31"

    payload = _analysis(as_of)

    equity = _class(payload, "equity")
    bond = _class(payload, "fixed_income")
    equity_account = _account_row(payload, analysis_live.account_id)
    bond_account = _account_row(payload, analysis_live.other_account_id)
    assert equity["xirrReason"] is None
    assert equity["valueBase"] == "24.0000"
    assert equity_account["xirrReason"] is None
    assert bond["xirrReason"] == "missing_price"
    assert bond["valueBase"] is None
    assert bond_account["xirrReason"] == "missing_price"
    assert bond_account["valueBase"] is None
    # The whole portfolio also fails closed: it holds the unpriced bond too.
    assert payload["inception"]["dietzReason"] == "missing_price"


def test_a_cross_class_switch_is_incomplete_for_both_classes_but_the_account_stays_complete(
    analysis_live: AnalysisFixture,
) -> None:
    """The switch's own counterpart holding is in the same account but a different asset class
    (spec "Class and account returns")."""
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        "2024-01-10",
        "10.00000000",
        "5.000000",
    )
    _switch(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        analysis_live.bond_security_id,
        "2024-01-15",
        "4.00000000",
        "5.000000",
        "2.00000000",
        "10.000000",
    )
    _price(analysis_live.equity_security_id, "2024-01-31", "6.000000")
    _price(analysis_live.bond_security_id, "2024-01-31", "11.000000")
    as_of = "2024-01-31"

    payload = _analysis(as_of)

    assert _class(payload, "equity")["xirrReason"] == "incomplete_history"
    assert _class(payload, "fixed_income")["xirrReason"] == "incomplete_history"
    account = _account_row(payload, analysis_live.account_id)
    assert account["xirrReason"] not in ("incomplete_history", "blocked_action")
    assert payload["inception"]["dietzReason"] is None


def test_a_cross_account_transfer_is_incomplete_for_both_accounts_but_the_class_stays_complete(
    analysis_live: AnalysisFixture,
) -> None:
    """The transfer's own counterpart holding is in a different account but the same asset
    class (spec "Class and account returns")."""
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        "2024-01-10",
        "10.00000000",
        "5.000000",
    )
    _platform_transfer(
        analysis_live,
        analysis_live.account_id,
        analysis_live.other_account_id,
        analysis_live.equity_security_id,
        "2024-01-15",
        "4.00000000",
    )
    _price(analysis_live.equity_security_id, "2024-01-31", "6.000000")
    as_of = "2024-01-31"

    payload = _analysis(as_of)

    assert _account_row(payload, analysis_live.account_id)["xirrReason"] == "incomplete_history"
    assert (
        _account_row(payload, analysis_live.other_account_id)["xirrReason"] == "incomplete_history"
    )
    assert _class(payload, "equity")["xirrReason"] not in ("incomplete_history", "blocked_action")
    assert payload["inception"]["dietzReason"] is None


def test_coverage_lists_the_snapshot_account_without_scoring_it(
    analysis_live: AnalysisFixture,
) -> None:
    payload = _analysis("2024-01-31")

    [row] = payload["coverage"]
    assert row["accountId"] == str(analysis_live.retirement_account_id)
    assert row["valuationMode"] == "snapshot"
    assert all(
        a["accountId"] != str(analysis_live.retirement_account_id) for a in payload["accounts"]
    )


def test_no_history_before_any_refresh_states_the_reason_and_is_empty(
    analysis_live: AnalysisFixture,
) -> None:
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        "2024-01-10",
        "4.00000000",
        "5.000000",
    )
    _price(analysis_live.equity_security_id, "2024-01-31", "6.000000")

    payload = _analysis("2024-01-31")

    assert payload["wealthHistoryReason"] == "wealth_history_not_refreshed"
    assert payload["months"] == []
    assert payload["years"] == []
    assert payload["target"] == {"source": "none", "groups": []}


def test_month_and_year_dietz_match_the_formula_and_class_lines_sum_to_the_portfolio_line(
    analysis_live: AnalysisFixture,
) -> None:
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        "2024-01-10",
        "4.00000000",
        "5.000000",
    )
    _acquire(
        analysis_live,
        analysis_live.other_account_id,
        analysis_live.bond_security_id,
        "2024-01-10",
        "3.00000000",
        "10.000000",
    )
    _price(analysis_live.equity_security_id, "2024-01-31", "6.000000")
    _price(analysis_live.bond_security_id, "2024-01-31", "11.000000")
    _price(analysis_live.equity_security_id, "2024-02-29", "6.500000")
    _price(analysis_live.bond_security_id, "2024-02-29", "11.500000")
    _refresh()

    payload = _analysis("2024-02-29")

    assert payload["wealthHistoryReason"] is None
    # `refresh` always computes through today's own last completed month (the route hardcodes
    # `date.today()`), so the response may carry many months beyond February 2024 -- every one
    # of them flat once no later price ever moves it. Only January and February are asserted.
    by_month = {m["monthEnd"]: m for m in payload["months"]}
    assert {"2024-01-31", "2024-02-29"} <= set(by_month)
    january = by_month["2024-01-31"]
    assert january["complete"] is True

    expected_january_portfolio = _expected_dietz_percent(
        [("2024-01-10", Decimal("-20.0000")), ("2024-01-10", Decimal("-30.0000"))],
        date(2023, 12, 31),
        date(2024, 1, 31),
        Decimal(0),
        Decimal("57.0000"),
    )
    assert january["portfolio"]["dietzPercent"] == str(expected_january_portfolio)

    for field in (
        "opening",
        "closing",
        "contributions",
        "withdrawals",
        "income",
        "charges",
        "marketAndFx",
    ):
        class_sum = sum(
            (Decimal(c[field]) for c in january["classes"] if c[field] is not None), Decimal(0)
        )
        assert Decimal(january["portfolio"][field]) == class_sum, field

    february = by_month["2024-02-29"]
    assert february["complete"] is True
    expected_february_portfolio = _expected_dietz_percent(
        [], date(2024, 1, 31), date(2024, 2, 29), Decimal("57.0000"), Decimal("60.5000")
    )
    assert february["portfolio"]["dietzPercent"] == str(expected_february_portfolio)

    [year] = [y for y in payload["years"] if y["year"] == 2024]
    assert year["incompleteMonths"] == 0
    assert year["portfolio"]["contributions"] == "50.0000"
    assert year["portfolio"]["opening"] == "0.0000"
    assert year["portfolio"]["closing"] == "60.5000"

    assert payload["target"]["source"] == "inferred"
    ratios = {g["label"]: Decimal(g["ratio"]) for g in payload["target"]["groups"]}
    assert sum(ratios.values()) == Decimal("1.0000000000")
    assert ratios["fixed_income"] > ratios["equity"]


def test_allocation_targets_put_get_delete_and_a_bad_put_leaves_the_previous_set(
    analysis_live: AnalysisFixture,
) -> None:
    empty = _client().get("/portfolio/allocation-targets")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"groups": []}

    put = _client().put(
        "/portfolio/allocation-targets",
        json={
            "groups": [
                {"label": "equity", "ratio": "0.6000000000"},
                {"label": "fixed_income", "ratio": "0.4000000000"},
            ]
        },
        headers=ORIGIN,
    )
    assert put.status_code == 200, put.text
    stored = {g["label"]: g["ratio"] for g in put.json()["groups"]}
    assert stored == {"equity": "0.6000000000", "fixed_income": "0.4000000000"}

    over_precise = _client().put(
        "/portfolio/allocation-targets",
        json={
            "groups": [
                {"label": "equity", "ratio": "0.60000000001"},
                {"label": "fixed_income", "ratio": "0.39999999999"},
            ]
        },
        headers=ORIGIN,
    )
    assert over_precise.status_code == 422, over_precise.text

    bad_sum = _client().put(
        "/portfolio/allocation-targets",
        json={
            "groups": [
                {"label": "equity", "ratio": "0.5000000000"},
                {"label": "fixed_income", "ratio": "0.4000000000"},
            ]
        },
        headers=ORIGIN,
    )
    assert bad_sum.status_code == 422, bad_sum.text

    still_stored = _client().get("/portfolio/allocation-targets")
    assert {g["label"]: g["ratio"] for g in still_stored.json()["groups"]} == stored

    unknown_label = _client().put(
        "/portfolio/allocation-targets",
        json={"groups": [{"label": "not_a_class", "ratio": "1.0000000000"}]},
        headers=ORIGIN,
    )
    assert unknown_label.status_code == 422, unknown_label.text

    deleted = _client().delete("/portfolio/allocation-targets", headers=ORIGIN)
    assert deleted.status_code == 204, deleted.text
    after_delete = _client().get("/portfolio/allocation-targets")
    assert after_delete.json() == {"groups": []}


def test_benchmark_put_get_delete_and_scope_validation(analysis_live: AnalysisFixture) -> None:
    portfolio_put = _client().put(
        "/portfolio/benchmarks/portfolio",
        json={"securityId": str(analysis_live.equity_security_id)},
        headers=ORIGIN,
    )
    assert portfolio_put.status_code == 200, portfolio_put.text
    assert portfolio_put.json() == {
        "scopeKind": "portfolio",
        "assetClass": None,
        "securityId": str(analysis_live.equity_security_id),
        "securityName": "Synthetic equity fund",
    }

    missing_class = _client().put(
        "/portfolio/benchmarks/asset_class",
        json={"securityId": str(analysis_live.bond_security_id)},
        headers=ORIGIN,
    )
    assert missing_class.status_code == 422, missing_class.text

    scope_mismatch = _client().put(
        "/portfolio/benchmarks/portfolio",
        params={"assetClass": "equity"},
        json={"securityId": str(analysis_live.equity_security_id)},
        headers=ORIGIN,
    )
    assert scope_mismatch.status_code == 422, scope_mismatch.text

    bad_label = _client().put(
        "/portfolio/benchmarks/asset_class",
        params={"assetClass": "not_a_class"},
        json={"securityId": str(analysis_live.bond_security_id)},
        headers=ORIGIN,
    )
    assert bad_label.status_code == 422, bad_label.text

    foreign_security = _client().put(
        "/portfolio/benchmarks/asset_class",
        params={"assetClass": "fixed_income"},
        json={"securityId": str(analysis_live.other_security_id)},
        headers=ORIGIN,
    )
    assert foreign_security.status_code == 422, foreign_security.text

    class_put = _client().put(
        "/portfolio/benchmarks/asset_class",
        params={"assetClass": "fixed_income"},
        json={"securityId": str(analysis_live.bond_security_id)},
        headers=ORIGIN,
    )
    assert class_put.status_code == 200, class_put.text

    listed = _client().get("/portfolio/benchmarks")
    assert {(b["scopeKind"], b["assetClass"]) for b in listed.json()["benchmarks"]} == {
        ("portfolio", None),
        ("asset_class", "fixed_income"),
    }

    unknown_delete = _client().delete(
        "/portfolio/benchmarks/asset_class", params={"assetClass": "cash"}, headers=ORIGIN
    )
    assert unknown_delete.status_code == 404, unknown_delete.text

    deleted = _client().delete("/portfolio/benchmarks/portfolio", headers=ORIGIN)
    assert deleted.status_code == 204, deleted.text
    after_delete = _client().get("/portfolio/benchmarks")
    assert {(b["scopeKind"], b["assetClass"]) for b in after_delete.json()["benchmarks"]} == {
        ("asset_class", "fixed_income")
    }


def test_analysis_reports_a_class_benchmark_and_excess(analysis_live: AnalysisFixture) -> None:
    _acquire(
        analysis_live,
        analysis_live.account_id,
        analysis_live.equity_security_id,
        "2024-01-10",
        "4.00000000",
        "5.000000",
    )
    _price(analysis_live.equity_security_id, "2024-01-31", "6.000000")
    _price(analysis_live.bond_security_id, "2024-01-01", "10.000000")
    _price(analysis_live.bond_security_id, "2024-01-31", "10.500000")
    bench = _client().put(
        "/portfolio/benchmarks/asset_class",
        params={"assetClass": "equity"},
        json={"securityId": str(analysis_live.bond_security_id)},
        headers=ORIGIN,
    )
    assert bench.status_code == 200, bench.text

    payload = _analysis("2024-01-31")

    equity = _class(payload, "equity")
    assert equity["inception"]["benchmarkDietzReason"] is None
    assert equity["inception"]["benchmarkDietzPercent"] is not None
    assert equity["inception"]["excessPercent"] is not None
    expected_excess = (
        Decimal(equity["inception"]["dietzPercent"])
        - Decimal(equity["inception"]["benchmarkDietzPercent"])
    ).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
    assert Decimal(equity["inception"]["excessPercent"]) == expected_excess


def test_every_new_write_is_owner_only(analysis_live: AnalysisFixture) -> None:
    target_put = _client().put(
        "/portfolio/allocation-targets",
        json={"groups": [{"label": "equity", "ratio": "1.0000000000"}]},
        headers=ORIGIN,
    )
    assert target_put.status_code == 200, target_put.text
    benchmark_put = _client().put(
        "/portfolio/benchmarks/portfolio",
        json={"securityId": str(analysis_live.equity_security_id)},
        headers=ORIGIN,
    )
    assert benchmark_put.status_code == 200, benchmark_put.text

    app.dependency_overrides[auth.require_owner] = _member
    writes = [
        _client().put(
            "/portfolio/allocation-targets",
            json={"groups": [{"label": "cash", "ratio": "1.0000000000"}]},
            headers=ORIGIN,
        ),
        _client().delete("/portfolio/allocation-targets", headers=ORIGIN),
        _client().put(
            "/portfolio/benchmarks/portfolio",
            json={"securityId": str(analysis_live.bond_security_id)},
            headers=ORIGIN,
        ),
        _client().delete("/portfolio/benchmarks/portfolio", headers=ORIGIN),
    ]
    app.dependency_overrides[auth.require_owner] = lambda: analysis_live.owner
    assert [r.status_code for r in writes] == [403] * 4

    unchanged_targets = _client().get("/portfolio/allocation-targets")
    assert {g["label"] for g in unchanged_targets.json()["groups"]} == {"equity"}
    unchanged_benchmarks = _client().get("/portfolio/benchmarks")
    assert [b["securityId"] for b in unchanged_benchmarks.json()["benchmarks"]] == [
        str(analysis_live.equity_security_id)
    ]


def test_another_household_never_sees_this_households_targets_or_benchmarks(
    analysis_live: AnalysisFixture,
) -> None:
    put_target = _client().put(
        "/portfolio/allocation-targets",
        json={"groups": [{"label": "equity", "ratio": "1.0000000000"}]},
        headers=ORIGIN,
    )
    assert put_target.status_code == 200, put_target.text
    put_benchmark = _client().put(
        "/portfolio/benchmarks/portfolio",
        json={"securityId": str(analysis_live.equity_security_id)},
        headers=ORIGIN,
    )
    assert put_benchmark.status_code == 200, put_benchmark.text

    app.dependency_overrides[auth.require_owner] = lambda: analysis_live.other_owner
    other_targets = _client().get("/portfolio/allocation-targets")
    other_benchmarks = _client().get("/portfolio/benchmarks")
    app.dependency_overrides[auth.require_owner] = lambda: analysis_live.owner

    assert other_targets.json() == {"groups": []}
    assert other_benchmarks.json() == {"benchmarks": []}
