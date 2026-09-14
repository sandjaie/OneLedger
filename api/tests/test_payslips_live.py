"""Live owner-scoped payslip, PDF import and net-pay link tests (Phase 1.7 Task 1).

Skipped when no database is reachable. Every name, label and amount is synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .fixtures.payslip_pdf import PayslipMonthSpec, build_payslip_pdf
from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class PayslipFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    account_id: UUID
    other_account_id: UUID
    secondary_account_id: UUID
    currency: str
    secondary_currency: str


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
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[PayslipFixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "payslip-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-payslip-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = found
        for household, name in (
            (household_id, "payslip household"),
            (other_household_id, "other payslip household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "payslip owner"),
            (other_person_id, other_household_id, "other payslip owner"),
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
        account_id = _account(cur, household_id, person_id, "Synthetic payslip account", base)
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic payslip account", base
        )
        secondary_account_id = _account(
            cur, household_id, person_id, "Synthetic secondary-currency account", secondary
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield PayslipFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            account_id=account_id,
            other_account_id=other_account_id,
            secondary_account_id=secondary_account_id,
            currency=str(base).strip(),
            secondary_currency=str(secondary).strip(),
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            # payslip/payslip_line are this branch's new tables; document, transaction and
            # transfer are emptied too, matching every other `*_live.py` fixture, because
            # `connect_to_test_database()` refuses to start a later suite's fixture against a
            # database still holding committed transaction rows.
            cur.execute("TRUNCATE document, transaction, transfer, payslip_line, payslip CASCADE")
        connection.commit()
        connection.close()


def _body(fixture: PayslipFixture, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "payMonth": "2026-01",
        "currency": fixture.currency,
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


def _income(
    fixture: PayslipFixture,
    *,
    amount: str,
    on: date,
    currency: str | None = None,
    account_id: UUID | None = None,
    household_id: UUID | None = None,
) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, amount, currency,
                   fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                   amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, %s, 'SYNTHETIC SALARY CREDIT', %s, %s, %s, 1, 1, %s, %s,
                       'income', 'manual', %s) RETURNING id""",
            (
                household_id or fixture.owner.household_id,
                account_id or fixture.account_id,
                on,
                Decimal(amount),
                currency or fixture.currency,
                on,
                Decimal(amount),
                Decimal(amount),
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()
        assert found is not None
    fixture.connection.commit()
    return UUID(str(found[0]))


def _payslip_count(fixture: PayslipFixture) -> int:
    with fixture.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM payslip WHERE household_id = %s", (fixture.owner.household_id,)
        )
        found = cur.fetchone()
        assert found is not None
        return int(found[0])


def _document_count(fixture: PayslipFixture) -> int:
    with fixture.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM document
                WHERE household_id = %s AND document_type = 'payslip'""",
            (fixture.owner.household_id,),
        )
        found = cur.fetchone()
        assert found is not None
        return int(found[0])


def test_entry_replay_conflicting_key_and_exact_round_trip(live: PayslipFixture) -> None:
    key = str(uuid4())
    body = _body(live, idempotencyKey=key)

    created = client().post("/payslips", json=body, headers=ORIGIN)
    assert created.status_code == 201
    row = created.json()
    assert row["payMonth"] == "2026-01"
    assert row["grossEarnings"] == "5000.0000"
    assert row["totalDeductions"] == "1000.0000"
    assert row["netPay"] == "4000.0000"
    assert row["imported"] is False
    assert row["deposit"] is None

    replay = client().post("/payslips", json=body, headers=ORIGIN)
    assert replay.status_code == 200
    assert replay.json()["id"] == row["id"]
    assert _payslip_count(live) == 1

    conflicting = client().post(
        "/payslips", json=_body(live, idempotencyKey=key, payMonth="2026-02"), headers=ORIGIN
    )
    assert conflicting.status_code == 409

    excess_precision = client().post(
        "/payslips",
        json=_body(live, idempotencyKey=str(uuid4()), grossEarnings="5000.00001"),
        headers=ORIGIN,
    )
    assert excess_precision.status_code == 422
    assert _payslip_count(live) == 1


def test_non_reconciling_entry_stores_nothing(live: PayslipFixture) -> None:
    mismatched = _body(
        live,
        idempotencyKey=str(uuid4()),
        lines=[
            {"kind": "earning", "label": "Basic", "amount": "4000.0000"},
            {"kind": "deduction", "label": "Tax", "amount": "1000.0000"},
        ],
    )
    response = client().post("/payslips", json=mismatched, headers=ORIGIN)
    assert response.status_code == 422
    assert _payslip_count(live) == 0


def test_foreign_payslip_and_credit_are_not_found(live: PayslipFixture) -> None:
    payslip = (
        client()
        .post("/payslips", json=_body(live, idempotencyKey=str(uuid4())), headers=ORIGIN)
        .json()
    )

    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        response = client().put(
            f"/payslips/{payslip['id']}/deposit",
            json={"transactionId": str(uuid4())},
            headers=ORIGIN,
        )
        assert response.status_code == 404
        response = client().get(f"/payslips/{payslip['id']}/deposit-candidates", headers=ORIGIN)
        assert response.status_code == 404
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live.owner

    foreign_credit = _income(
        live,
        amount="4000.0000",
        on=date(2026, 1, 5),
        account_id=live.other_account_id,
        household_id=live.other_owner.household_id,
    )
    response = client().put(
        f"/payslips/{payslip['id']}/deposit",
        json={"transactionId": str(foreign_credit)},
        headers=ORIGIN,
    )
    assert response.status_code == 404


def test_corrections_supersede_without_editing_the_original(live: PayslipFixture) -> None:
    original = (
        client()
        .post("/payslips", json=_body(live, idempotencyKey=str(uuid4())), headers=ORIGIN)
        .json()
    )

    correction_key = str(uuid4())
    corrected_body = _body(live, idempotencyKey=correction_key, netPay="4000.0000")
    correction = client().post(
        f"/payslips/{original['id']}/corrections", json=corrected_body, headers=ORIGIN
    )
    assert correction.status_code == 201
    correction_row = correction.json()
    assert correction_row["supersedesPayslipId"] == original["id"]

    replay = client().post(
        f"/payslips/{original['id']}/corrections", json=corrected_body, headers=ORIGIN
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == correction_row["id"]

    conflicting = client().post(
        f"/payslips/{original['id']}/corrections",
        json=_body(live, idempotencyKey=correction_key, payMonth="2026-03"),
        headers=ORIGIN,
    )
    assert conflicting.status_code == 409

    second_correction = client().post(
        f"/payslips/{original['id']}/corrections",
        json=_body(live, idempotencyKey=str(uuid4())),
        headers=ORIGIN,
    )
    assert second_correction.status_code == 409

    listing = client().get("/payslips", headers=ORIGIN).json()["payslips"]
    original_row = next(p for p in listing if p["id"] == original["id"])
    assert original_row["supersededByPayslipId"] == correction_row["id"]
    assert original_row["grossEarnings"] == "5000.0000"
    assert original_row["netPay"] == "4000.0000"

    credit = _income(live, amount="4000.0000", on=date(2026, 1, 5))
    link = client().put(
        f"/payslips/{correction_row['id']}/deposit",
        json={"transactionId": str(credit)},
        headers=ORIGIN,
    )
    assert link.status_code == 200
    blocked = client().post(
        f"/payslips/{correction_row['id']}/corrections",
        json=_body(live, idempotencyKey=str(uuid4())),
        headers=ORIGIN,
    )
    assert blocked.status_code == 409


def test_import_preview_writes_nothing_then_commit_imports_reconciling_months(
    live: PayslipFixture,
) -> None:
    manual_april = client().post(
        "/payslips",
        json=_body(
            live,
            idempotencyKey=str(uuid4()),
            payMonth="2026-04",
            grossEarnings="3400.0000",
            totalDeductions="460.0000",
            netPay="2940.0000",
            lines=[
                {"kind": "earning", "label": "Basic", "amount": "3400.0000"},
                {"kind": "deduction", "label": "Tax", "amount": "460.0000"},
            ],
        ),
        headers=ORIGIN,
    )
    assert manual_april.status_code == 201

    months = [
        PayslipMonthSpec(
            month_name="JANUARY",
            year=2026,
            currency=live.currency,
            payment_lines=[("Basic", "3000.00")],
            deduction_lines=[("Tax", "400.00")],
            payments_total="3000.00",
            deductions_total="400.00",
            net_pay="2600.00",
            include_detail_page=True,
        ),
        PayslipMonthSpec(
            month_name="FEBRUARY",
            year=2026,
            currency=live.currency,
            payment_lines=[("Basic", "3000.00")],
            deduction_lines=[("Tax", "400.00")],
            payments_total="2900.00",  # deliberately wrong: does not reconcile
            deductions_total="400.00",
            net_pay="2500.00",
        ),
        PayslipMonthSpec(
            month_name="MARCH",
            year=2026,
            currency=live.currency,
            payment_lines=[("Basic", "3200.00")],
            deduction_lines=[("Tax", "450.00")],
            payments_total="3200.00",
            deductions_total="450.00",
            net_pay="2750.00",
        ),
        PayslipMonthSpec(
            month_name="APRIL",
            year=2026,
            currency=live.currency,
            payment_lines=[("Basic", "3400.00")],
            deduction_lines=[("Tax", "460.00")],
            payments_total="3400.00",
            deductions_total="460.00",
            net_pay="2940.00",
        ),
    ]
    pdf_bytes = build_payslip_pdf(months)

    preview = client().post(
        "/payslips/import",
        files={"file": ("payslips.pdf", pdf_bytes)},
        data={"commit": "false"},
        headers=ORIGIN,
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["supported"]
    assert [m["status"] for m in preview_body["months"]] == [None, None, None, None]
    assert preview_body["months"][1]["reason"] == "payments_total_disagrees"
    assert _payslip_count(live) == 1  # only the manual April entry
    assert _document_count(live) == 0  # a preview writes nothing (OL-004)

    # January carries a detail page that repeats its own "CONFIDENTIAL" / "PAY ADVICE FOR"
    # marker (OL-002); its lines must still be exactly what was extracted before that page
    # existed.
    january = next(m for m in preview_body["months"] if m["payMonth"] == "2026-01")
    assert [(line["kind"], line["label"], line["amount"]) for line in january["lines"]] == [
        ("earning", "Basic", "3000.00"),
        ("deduction", "Tax", "400.00"),
    ]

    committed = client().post(
        "/payslips/import",
        files={"file": ("payslips.pdf", pdf_bytes)},
        data={"commit": "true"},
        headers=ORIGIN,
    )
    assert committed.status_code == 200
    outcomes = [m["status"] for m in committed.json()["months"]]
    assert outcomes == ["imported", "not_reconciled", "imported", "already_recorded"]
    assert _payslip_count(live) == 3  # manual April, imported January and March
    assert _document_count(live) == 1  # one uploaded file, one document row (OL-004)

    with live.connection.cursor() as cur:
        cur.execute(
            """SELECT count(DISTINCT source_document_id) FROM payslip
                WHERE household_id = %s AND source_document_id IS NOT NULL""",
            (live.owner.household_id,),
        )
        found = cur.fetchone()
        assert found is not None and int(found[0]) == 1  # January and March share it

    reimport = client().post(
        "/payslips/import",
        files={"file": ("payslips.pdf", pdf_bytes)},
        data={"commit": "true"},
        headers=ORIGIN,
    )
    reimport_outcomes = [m["status"] for m in reimport.json()["months"]]
    assert reimport_outcomes == [
        "already_imported",
        "not_reconciled",
        "already_imported",
        "already_recorded",
    ]
    assert _payslip_count(live) == 3  # nothing new
    assert _document_count(live) == 1  # the same document is reused, not duplicated


def test_import_stores_no_document_when_nothing_reconciles(live: PayslipFixture) -> None:
    months = [
        PayslipMonthSpec(
            month_name="JANUARY",
            year=2026,
            currency=live.currency,
            payment_lines=[("Basic", "3000.00")],
            deduction_lines=[("Tax", "400.00")],
            payments_total="2900.00",  # deliberately wrong: does not reconcile
            deductions_total="400.00",
            net_pay="2500.00",
        ),
    ]
    committed = client().post(
        "/payslips/import",
        files={"file": ("payslips.pdf", build_payslip_pdf(months))},
        data={"commit": "true"},
        headers=ORIGIN,
    )
    assert [m["status"] for m in committed.json()["months"]] == ["not_reconciled"]
    assert _payslip_count(live) == 0
    assert _document_count(live) == 0  # OL-004: nothing reconciled, nothing stored


def test_import_stores_no_document_when_every_reconciling_month_is_already_recorded(
    live: PayslipFixture,
) -> None:
    manual = client().post(
        "/payslips",
        json=_body(
            live,
            idempotencyKey=str(uuid4()),
            payMonth="2026-01",
            grossEarnings="3000.0000",
            totalDeductions="400.0000",
            netPay="2600.0000",
            lines=[
                {"kind": "earning", "label": "Basic", "amount": "3000.0000"},
                {"kind": "deduction", "label": "Tax", "amount": "400.0000"},
            ],
        ),
        headers=ORIGIN,
    )
    assert manual.status_code == 201
    assert _document_count(live) == 0

    months = [
        PayslipMonthSpec(
            month_name="JANUARY",
            year=2026,
            currency=live.currency,
            payment_lines=[("Basic", "3000.00")],
            deduction_lines=[("Tax", "400.00")],
            payments_total="3000.00",
            deductions_total="400.00",
            net_pay="2600.00",
        ),
    ]
    committed = client().post(
        "/payslips/import",
        files={"file": ("payslips.pdf", build_payslip_pdf(months))},
        data={"commit": "true"},
        headers=ORIGIN,
    )
    assert [m["status"] for m in committed.json()["months"]] == ["already_recorded"]
    assert _payslip_count(live) == 1  # only the manual entry
    # OL-005: a fresh file whose only reconciling month was already recorded leaves no orphan
    # document row behind.
    assert _document_count(live) == 0


def test_deposit_candidates_window_link_and_unlink(live: PayslipFixture) -> None:
    payslip = (
        client()
        .post("/payslips", json=_body(live, idempotencyKey=str(uuid4())), headers=ORIGIN)
        .json()
    )

    matching = _income(live, amount="4000.0000", on=date(2026, 1, 1))  # first day of pay month
    edge = _income(live, amount="4000.0000", on=date(2026, 2, 10))  # 10th of the next month
    excluded = _income(live, amount="4000.0000", on=date(2026, 2, 11))  # 11th: excluded
    wrong_amount = _income(live, amount="3999.0000", on=date(2026, 1, 10))
    wrong_currency = _income(
        live,
        amount="4000.0000",
        on=date(2026, 1, 10),
        currency=live.secondary_currency,
        account_id=live.secondary_account_id,
    )
    assert excluded and wrong_amount and wrong_currency  # created only to prove exclusion

    candidates = client().get(f"/payslips/{payslip['id']}/deposit-candidates", headers=ORIGIN)
    assert candidates.status_code == 200
    ids = [c["transactionId"] for c in candidates.json()["candidates"]]
    assert ids == [str(matching), str(edge)]

    link = client().put(
        f"/payslips/{payslip['id']}/deposit",
        json={"transactionId": str(matching)},
        headers=ORIGIN,
    )
    assert link.status_code == 200
    assert link.json()["deposit"]["transactionId"] == str(matching)
    assert link.json()["deposit"]["eligible"] is True

    relink = client().put(
        f"/payslips/{payslip['id']}/deposit",
        json={"transactionId": str(matching)},
        headers=ORIGIN,
    )
    assert relink.status_code == 200

    different = client().put(
        f"/payslips/{payslip['id']}/deposit",
        json={"transactionId": str(edge)},
        headers=ORIGIN,
    )
    assert different.status_code == 409

    other_payslip = (
        client()
        .post(
            "/payslips",
            json=_body(live, idempotencyKey=str(uuid4()), payMonth="2026-02", netPay="4000.0000"),
            headers=ORIGIN,
        )
        .json()
    )
    claimed_elsewhere = client().put(
        f"/payslips/{other_payslip['id']}/deposit",
        json={"transactionId": str(matching)},
        headers=ORIGIN,
    )
    assert claimed_elsewhere.status_code == 409

    unlink = client().delete(f"/payslips/{payslip['id']}/deposit", headers=ORIGIN)
    assert unlink.status_code == 200
    assert unlink.json()["deposit"] is None

    unlink_again = client().delete(f"/payslips/{payslip['id']}/deposit", headers=ORIGIN)
    assert unlink_again.status_code == 200


def test_superseded_payslip_cannot_be_linked(live: PayslipFixture) -> None:
    original = (
        client()
        .post("/payslips", json=_body(live, idempotencyKey=str(uuid4())), headers=ORIGIN)
        .json()
    )
    client().post(
        f"/payslips/{original['id']}/corrections",
        json=_body(live, idempotencyKey=str(uuid4())),
        headers=ORIGIN,
    )
    credit = _income(live, amount="4000.0000", on=date(2026, 1, 1))
    response = client().put(
        f"/payslips/{original['id']}/deposit",
        json={"transactionId": str(credit)},
        headers=ORIGIN,
    )
    assert response.status_code == 409


def test_wrong_amount_is_refused_by_the_link_itself(live: PayslipFixture) -> None:
    payslip = (
        client()
        .post("/payslips", json=_body(live, idempotencyKey=str(uuid4())), headers=ORIGIN)
        .json()
    )
    wrong_amount = _income(live, amount="3999.0000", on=date(2026, 1, 1))
    refused = client().put(
        f"/payslips/{payslip['id']}/deposit",
        json={"transactionId": str(wrong_amount)},
        headers=ORIGIN,
    )
    assert refused.status_code == 422


def test_linking_changes_no_reportable_income_figure(live: PayslipFixture) -> None:
    payslip = (
        client()
        .post("/payslips", json=_body(live, idempotencyKey=str(uuid4())), headers=ORIGIN)
        .json()
    )
    credit = _income(live, amount="4000.0000", on=date(2026, 1, 1))

    def reportable_income_total() -> Decimal:
        with live.connection.cursor() as cur:
            cur.execute(
                """SELECT coalesce(sum(amount), 0) FROM v_transaction_reportable
                    WHERE household_id = %s AND txn_type = 'income'""",
                (live.owner.household_id,),
            )
            found = cur.fetchone()
            assert found is not None
            return Decimal(found[0])

    before = reportable_income_total()
    link = client().put(
        f"/payslips/{payslip['id']}/deposit",
        json={"transactionId": str(credit)},
        headers=ORIGIN,
    )
    assert link.status_code == 200
    assert reportable_income_total() == before  # linking writes no transaction row


def test_deposit_becomes_ineligible_once_the_credit_is_a_transfer_leg(
    live: PayslipFixture,
) -> None:
    with live.connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM person WHERE household_id = %s AND is_self", (live.owner.household_id,)
        )
        found = cur.fetchone()
        assert found is not None
        person_id = UUID(str(found[0]))
        debit_account_id = _account(
            cur, live.owner.household_id, person_id, "Synthetic debit account", live.currency
        )
    live.connection.commit()

    payslip = (
        client()
        .post("/payslips", json=_body(live, idempotencyKey=str(uuid4())), headers=ORIGIN)
        .json()
    )
    credit = _income(live, amount="4000.0000", on=date(2026, 1, 1))
    link = client().put(
        f"/payslips/{payslip['id']}/deposit",
        json={"transactionId": str(credit)},
        headers=ORIGIN,
    )
    assert link.status_code == 200
    assert link.json()["deposit"]["eligible"] is True

    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, amount, currency,
                   fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                   amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, '2026-01-01', 'SYNTHETIC TRANSFER OUT', -4000.0000, %s,
                       '2026-01-01', 1, 1, -4000.0000, -4000.0000, 'expense', 'manual', %s)
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
            "destinationTransactionId": str(credit),
            "fromCandidate": False,
        },
        headers=ORIGIN,
    )
    assert confirm.status_code == 201

    listing = client().get("/payslips", headers=ORIGIN).json()["payslips"]
    row = next(p for p in listing if p["id"] == payslip["id"])
    assert row["deposit"]["transactionId"] == str(credit)
    assert row["deposit"]["eligible"] is False
