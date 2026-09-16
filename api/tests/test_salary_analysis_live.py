"""Live owner-scoped salary analysis and earning-rule tests (Phase 4.2 Task 1).

Skipped when no database is reachable. Every name, label and amount is synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class SalaryFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    account_id: UUID
    other_account_id: UUID
    currency: str


def client() -> TestClient:
    return TestClient(app)


def _account(
    cur: psycopg.Cursor[Any], household_id: UUID, person_id: UUID, name: str, currency: str
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                country, opening_balance, opening_balance_date)
           VALUES (%s, %s, %s, 'savings', %s, 'XZ', 0, '2026-01-01') RETURNING id""",
        (household_id, person_id, name, currency),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


@pytest.fixture
def live() -> Iterator[SalaryFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "salary-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-salary-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base = str(found[0]).strip()
        cur.execute("SELECT min(code) FROM currency WHERE code > %s", (base,))
        found = cur.fetchone()
        assert found is not None
        secondary = str(found[0]).strip()
        for household, name in (
            (household_id, "salary household"),
            (other_household_id, "other salary household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "salary owner"),
            (other_person_id, other_household_id, "other salary owner"),
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
        account_id = _account(cur, household_id, person_id, "Synthetic salary account", base)
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic salary account", base
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield SalaryFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            account_id=account_id,
            other_account_id=other_account_id,
            currency=base,
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute(
                """TRUNCATE document, transaction, transfer, payslip_line_posting,
                            payslip_deduction_rule, payslip_earning_rule, payslip_line, payslip
                            CASCADE"""
            )
        connection.commit()
        connection.close()


def _payslip_body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "payMonth": "2026-01",
        "currency": "___",  # overwritten by every caller below
        "grossEarnings": "5000.0000",
        "totalDeductions": "1000.0000",
        "netPay": "4000.0000",
        "lines": [
            {"kind": "earning", "label": "Basic", "amount": "5000.0000"},
            {"kind": "deduction", "label": "Tax", "amount": "1000.0000"},
        ],
        "idempotencyKey": str(uuid4()),
    }
    base.update(overrides)
    return base


def _create_payslip(fixture: SalaryFixture, **overrides: object) -> dict[str, Any]:
    overrides.setdefault("idempotencyKey", str(uuid4()))
    overrides.setdefault("currency", fixture.currency)
    body = _payslip_body(**overrides)
    response = client().post("/payslips", json=body, headers=ORIGIN)
    assert response.status_code in (200, 201), response.json()
    return cast("dict[str, Any]", response.json())


def _income(
    fixture: SalaryFixture,
    *,
    amount: str,
    on: date,
    account_id: UUID | None = None,
    household_id: UUID | None = None,
    currency: str | None = None,
    category_id: UUID | None = None,
) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, amount, currency,
                   fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                   amount_secondary, txn_type, category_id, source, dedupe_hash)
               VALUES (%s, %s, %s, 'SYNTHETIC SALARY CREDIT', %s, %s, %s, 1, 1, %s, %s,
                       'income', %s, 'manual', %s) RETURNING id""",
            (
                household_id or fixture.owner.household_id,
                account_id or fixture.account_id,
                on,
                Decimal(amount),
                currency or fixture.currency,
                on,
                Decimal(amount),
                Decimal(amount),
                category_id,
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()
        assert found is not None
    fixture.connection.commit()
    return UUID(str(found[0]))


def _seed_fx_rate(fixture: SalaryFixture, on: date) -> None:
    with fixture.connection.cursor() as cur:
        cur.execute(
            "SELECT secondary_currency FROM household WHERE id = %s", (fixture.owner.household_id,)
        )
        found = cur.fetchone()
        assert found is not None
        secondary = str(found[0]).strip()
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES (%s, %s, %s, 1, 'manual')
               ON CONFLICT (rate_date, base_currency, quote_currency) DO NOTHING""",
            (on, secondary, fixture.currency),
        )
    fixture.connection.commit()


def _category_id(fixture: SalaryFixture, slug: str) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT id FROM category WHERE slug = %s AND household_id IS NULL", (slug,))
        found = cur.fetchone()
        assert found is not None
        return UUID(str(found[0]))


def _analysis(fixture: SalaryFixture | None = None) -> dict[str, Any]:
    response = client().get("/salary/analysis", headers=ORIGIN)
    assert response.status_code == 200, response.json()
    return cast("dict[str, Any]", response.json())


def _currency_block(analysis: dict[str, Any], currency: str) -> dict[str, Any]:
    return next(c for c in analysis["currencies"] if c["currency"] == currency)


def test_superseded_payslip_is_excluded_from_analysis(live: SalaryFixture) -> None:
    original = _create_payslip(live, payMonth="2026-01")
    client().post(
        f"/payslips/{original['id']}/corrections",
        json=_payslip_body(
            currency=live.currency,
            netPay="4200.0000",
            grossEarnings="5200.0000",
            lines=[
                {"kind": "earning", "label": "Basic", "amount": "5200.0000"},
                {"kind": "deduction", "label": "Tax", "amount": "1000.0000"},
            ],
            idempotencyKey=str(uuid4()),
        ),
        headers=ORIGIN,
    )

    currency = _currency_block(_analysis(), live.currency)
    assert len(currency["payslips"]) == 1
    assert currency["payslips"][0]["netPay"] == "4200.0000"
    assert currency["years"][0]["netPay"] == "4200.0000"


def test_receipt_status_received_unlinked_and_ineligible(live: SalaryFixture) -> None:
    unlinked = _create_payslip(live, payMonth="2026-01")

    linked = _create_payslip(live, payMonth="2026-02")
    credit = _income(live, amount="4000.0000", on=date(2026, 2, 1))
    link = client().put(
        f"/payslips/{linked['id']}/deposit", json={"transactionId": str(credit)}, headers=ORIGIN
    )
    assert link.status_code == 200

    reclassified = _create_payslip(live, payMonth="2026-03")
    reclassified_credit = _income(live, amount="4000.0000", on=date(2026, 3, 1))
    client().put(
        f"/payslips/{reclassified['id']}/deposit",
        json={"transactionId": str(reclassified_credit)},
        headers=ORIGIN,
    )
    with live.connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM person WHERE household_id = %s AND is_self", (live.owner.household_id,)
        )
        found = cur.fetchone()
        assert found is not None
        person_id = UUID(str(found[0]))
        debit_account_id = _account(
            cur, live.owner.household_id, person_id, "Synthetic debit account two", live.currency
        )
    live.connection.commit()
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, amount, currency,
                   fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                   amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, '2026-03-01', 'SYNTHETIC TRANSFER OUT', -4000.0000, %s,
                       '2026-03-01', 1, 1, -4000.0000, -4000.0000, 'expense', 'manual', %s)
               RETURNING id""",
            (
                live.owner.household_id,
                debit_account_id,
                live.currency,
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()
        assert found is not None
        debit_id = UUID(str(found[0]))
    live.connection.commit()
    confirm = client().post(
        "/transfers",
        json={
            "sourceTransactionId": str(debit_id),
            "destinationTransactionId": str(reclassified_credit),
            "fromCandidate": False,
        },
        headers=ORIGIN,
    )
    assert confirm.status_code == 201

    currency = _currency_block(_analysis(), live.currency)
    by_id = {p["payslipId"]: p for p in currency["payslips"]}
    assert by_id[unlinked["id"]]["receiptStatus"] == "unlinked"
    assert by_id[unlinked["id"]]["cashReceived"] is None
    assert by_id[linked["id"]]["receiptStatus"] == "received"
    assert by_id[linked["id"]]["cashReceived"] == "4000.0000"
    assert by_id[reclassified["id"]]["receiptStatus"] == "ineligible"
    assert by_id[reclassified["id"]]["cashReceived"] is None

    by_year = {y["year"]: y for y in currency["years"]}
    assert by_year[2026]["cashReceived"] == "4000.0000"
    assert by_year[2026]["receivedMonths"] == 1


def test_salary_credits_without_payslip_excludes_linked_and_v44_income_leg(
    live: SalaryFixture,
) -> None:
    flights_category = _category_id(live, "travel-flights")
    salary_category = _category_id(live, "salary")

    unmatched = _income(live, amount="4500.0000", on=date(2026, 1, 5), category_id=salary_category)

    linked_payslip = _create_payslip(live, payMonth="2026-02")
    linked_credit = _income(live, amount="4000.0000", on=date(2026, 2, 1))
    client().put(
        f"/payslips/{linked_payslip['id']}/deposit",
        json={"transactionId": str(linked_credit)},
        headers=ORIGIN,
    )

    _seed_fx_rate(live, date(2026, 3, 15))
    deduction_payslip = _create_payslip(
        live,
        payMonth="2026-03",
        lines=[
            {"kind": "earning", "label": "Basic", "amount": "5000.0000"},
            {"kind": "deduction", "label": "Tax", "amount": "600.0000"},
            {"kind": "deduction", "label": "Flights", "amount": "400.0000"},
        ],
        totalDeductions="1000.0000",
    )
    deduction_credit = _income(live, amount="4000.0000", on=date(2026, 3, 15))
    client().put(
        f"/payslips/{deduction_payslip['id']}/deposit",
        json={"transactionId": str(deduction_credit)},
        headers=ORIGIN,
    )
    flights_line = next(line for line in deduction_payslip["lines"] if line["label"] == "Flights")
    spent = client().post(
        f"/payslips/{deduction_payslip['id']}/lines/{flights_line['lineNo']}/spending",
        json={"categoryId": str(flights_category)},
        headers=ORIGIN,
    )
    assert spent.status_code == 200, spent.json()

    analysis = _analysis()
    credits = analysis["salaryCreditsWithoutPayslip"]
    ids = {c["transactionId"] for c in credits}
    assert str(unmatched) in ids
    assert str(linked_credit) not in ids
    assert str(deduction_credit) not in ids
    # The V44 deduction pair's income leg (categorised Salary, booked on the same account/date
    # as the linked credit) must not double-count as an unmatched salary credit either.
    with live.connection.cursor() as cur:
        cur.execute(
            """SELECT income_transaction_id FROM payslip_line_posting
                WHERE household_id = %s""",
            (live.owner.household_id,),
        )
        found = cur.fetchone()
        assert found is not None
        posting_income_id = str(found[0])
    assert posting_income_id not in ids


def test_earning_rule_put_replay_delete_404_and_label_normalization(live: SalaryFixture) -> None:
    saved = client().put("/salary/earning-rules/Basic", json={"component": "fixed"}, headers=ORIGIN)
    assert saved.status_code == 200
    assert saved.json() == {"label": "Basic", "component": "fixed"}

    replay = client().put(
        "/salary/earning-rules/Basic", json={"component": "fixed"}, headers=ORIGIN
    )
    assert replay.status_code == 200

    updated = client().put(
        "/salary/earning-rules/Basic", json={"component": "variable"}, headers=ORIGIN
    )
    assert updated.status_code == 200
    assert updated.json()["component"] == "variable"

    listed = client().get("/salary/earning-rules", headers=ORIGIN)
    assert listed.json()["rules"] == [{"label": "Basic", "component": "variable"}]

    removed = client().delete("/salary/earning-rules/Basic", headers=ORIGIN)
    assert removed.status_code == 204

    removed_again = client().delete("/salary/earning-rules/Basic", headers=ORIGIN)
    assert removed_again.status_code == 404

    slashed = client().put(
        "/salary/earning-rules/Tax%2FNI", json={"component": "reimbursement"}, headers=ORIGIN
    )
    assert slashed.status_code == 200
    assert slashed.json()["label"] == "Tax/NI"
    assert client().delete("/salary/earning-rules/Tax%2FNI", headers=ORIGIN).status_code == 204

    too_long = "x" * 81
    invalid = client().put(
        f"/salary/earning-rules/{too_long}", json={"component": "fixed"}, headers=ORIGIN
    )
    assert invalid.status_code == 422


def test_a_rule_overrides_inference_and_removal_returns_to_it(live: SalaryFixture) -> None:
    for month in ("2026-01", "2026-02", "2026-03"):
        _create_payslip(live, payMonth=month)

    before = _currency_block(_analysis(), live.currency)
    basic_before = next(label for label in before["labels"] if label["label"] == "Basic")
    assert basic_before["component"] == "fixed"
    assert basic_before["source"] == "inferred"

    client().put("/salary/earning-rules/Basic", json={"component": "variable"}, headers=ORIGIN)
    overridden = _currency_block(_analysis(), live.currency)
    basic_overridden = next(label for label in overridden["labels"] if label["label"] == "Basic")
    assert basic_overridden["component"] == "variable"
    assert basic_overridden["source"] == "rule"

    client().delete("/salary/earning-rules/Basic", headers=ORIGIN)
    restored = _currency_block(_analysis(), live.currency)
    basic_restored = next(label for label in restored["labels"] if label["label"] == "Basic")
    assert basic_restored["component"] == "fixed"
    assert basic_restored["source"] == "inferred"


def test_other_household_data_is_invisible(live: SalaryFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        client().post(
            "/payslips",
            json=_payslip_body(currency=live.currency, idempotencyKey=str(uuid4())),
            headers=ORIGIN,
        )
        client().put("/salary/earning-rules/Basic", json={"component": "fixed"}, headers=ORIGIN)
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live.owner
    _income(
        live,
        amount="4500.0000",
        on=date(2026, 1, 5),
        account_id=live.other_account_id,
        household_id=live.other_owner.household_id,
    )

    analysis = _analysis()
    assert analysis["currencies"] == []
    assert analysis["salaryCreditsWithoutPayslip"] == []
    assert client().get("/salary/earning-rules", headers=ORIGIN).json()["rules"] == []


def test_get_routes_write_nothing(live: SalaryFixture) -> None:
    _create_payslip(live, payMonth="2026-01")
    client().put("/salary/earning-rules/Basic", json={"component": "fixed"}, headers=ORIGIN)

    def counts() -> tuple[int, int, int]:
        with live.connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM payslip")
            payslips = cur.fetchone()
            cur.execute("SELECT count(*) FROM payslip_earning_rule")
            rules = cur.fetchone()
            cur.execute("SELECT count(*) FROM transaction")
            transactions = cur.fetchone()
        assert payslips is not None and rules is not None and transactions is not None
        return int(payslips[0]), int(rules[0]), int(transactions[0])

    before = counts()
    for _ in range(2):
        assert client().get("/salary/analysis", headers=ORIGIN).status_code == 200
        assert client().get("/salary/earning-rules", headers=ORIGIN).status_code == 200
    assert counts() == before
