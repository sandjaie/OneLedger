"""Live owner-scoped tests for Phase 3.4 Task 2's commitments planned from register records:
P09 exactly (a claim's part-paid reimbursement, dashboard income unchanged, an income posting
refused), the per-kind schedule/sign/account/currency rules, the one-open-commitment and
two-source refusals, the maturity action item disappearing once planned, and household isolation
with a real foreign row. Skipped when no database is reachable. Every name, label and amount is
synthetic."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}
TODAY = date.today()


def _d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


@dataclass(frozen=True)
class RCFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    household_id: UUID
    other_household_id: UUID
    person_id: UUID
    asset_account_id: UUID
    deposit_account_id: UUID
    card_account_id: UUID
    other_account_id: UUID
    currency: str
    secondary_currency: str


def client() -> TestClient:
    return TestClient(app)


def _account(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    person_id: UUID,
    name: str,
    currency: str,
    *,
    account_type: str = "savings",
    credit_limit: str | None = None,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date, credit_limit)
           VALUES (%s, %s, %s, %s, %s, 'XZ', 0, '2026-01-01', %s) RETURNING id""",
        (household_id, person_id, name, account_type, currency, credit_limit),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


@pytest.fixture
def live() -> Iterator[RCFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "rc-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-rc-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = found
        for household, name in (
            (household_id, "rc household"),
            (other_household_id, "other rc household"),
        ):
            cur.execute(
                "INSERT INTO household (id, name, base_currency, secondary_currency) "
                "VALUES (%s, %s, %s, %s)",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "rc owner"),
            (other_person_id, other_household_id, "other rc owner"),
        ):
            cur.execute(
                "INSERT INTO person (id, household_id, display_name, is_self) "
                "VALUES (%s, %s, %s, true)",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, owner.email),
            (other_user_id, other_household_id, other_person_id, other_owner.email),
        ):
            cur.execute(
                "INSERT INTO app_user (id, household_id, person_id, email, role) "
                "VALUES (%s, %s, %s, %s, 'owner')",
                (user, household, person, email),
            )
        asset_account_id = _account(cur, household_id, person_id, "Synthetic rc asset", base)
        deposit_account_id = _account(
            cur, household_id, person_id, "Synthetic rc deposit", base, account_type="fixed_deposit"
        )
        card_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic rc card",
            base,
            account_type="credit_card",
            credit_limit="5000",
        )
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic rc asset", str(base).strip()
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield RCFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            household_id=household_id,
            other_household_id=other_household_id,
            person_id=person_id,
            asset_account_id=asset_account_id,
            deposit_account_id=deposit_account_id,
            card_account_id=card_account_id,
            other_account_id=other_account_id,
            currency=str(base).strip(),
            secondary_currency=str(secondary).strip(),
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE register_record, commitment, transaction CASCADE")
        connection.commit()
        connection.close()


def _register(kind: str, **fields: object) -> httpx.Response:
    body: dict[str, object] = {"kind": kind, "idempotencyKey": str(uuid4())}
    body.update(fields)
    response: httpx.Response = client().post("/register", json=body, headers=ORIGIN)
    return response


def _commitment(**fields: object) -> httpx.Response:
    body: dict[str, object] = {
        "label": "Synthetic register commitment",
        "endDate": None,
        "idempotencyKey": str(uuid4()),
    }
    body.update(fields)
    response: httpx.Response = client().post("/commitments", json=body, headers=ORIGIN)
    return response


def _raw_txn(
    fixture: RCFixture, account_id: UUID, amount: str, booked_date: str, txn_type: str
) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                       amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                       amount_base, amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, %s, 'SYNTHETIC RC POSTING', %s, %s, %s, 1, 1, %s, %s, %s,
                       'manual', %s)
               RETURNING id""",
            (
                fixture.owner.household_id,
                account_id,
                booked_date,
                amount,
                fixture.currency,
                booked_date,
                amount,
                amount,
                txn_type,
                f"rc-{uuid4()}",
            ),
        )
        row = cur.fetchone()
        assert row is not None
    fixture.connection.commit()
    return UUID(str(row[0]))


# ---------------------------------------------------------------------------------------
# P09 exactly: a 1,000 claim, a planned once reimbursement, a 400 refund part payment.


def test_p09_claim_part_payment_and_income_refusal(live: RCFixture) -> None:
    claim = _register(
        "claim",
        title="Synthetic P09 claim",
        counterparty="Synthetic insurer",
        amount="1000.0000",
        currency=live.currency,
        keyDate=_d(0),
    ).json()

    planned = _commitment(
        accountId=str(live.asset_account_id),
        amount="1000.0000",
        currency=live.currency,
        cadence="once",
        firstDate=claim["keyDate"],
        sourceRegisterRecordId=claim["id"],
        label="Synthetic reimbursement",
    )
    assert planned.status_code == 201, planned.text
    row = planned.json()
    assert row["source"] == "register_record"
    assert row["sourceRegisterRecordId"] == claim["id"]
    assert row["sourceRegisterTitle"] == "Synthetic P09 claim"
    commitment_id = row["id"]
    due_date = row["nextDueDate"]
    assert due_date == claim["keyDate"]

    before_dashboard = client().get(f"/dashboard?asOf={due_date}", headers=ORIGIN).json()
    income_before = before_dashboard["thisMonth"]["incomeBase"]

    refund_txn = _raw_txn(live, live.asset_account_id, "400.0000", due_date, txn_type="refund")
    part_payment = client().post(
        f"/commitments/{commitment_id}/resolutions",
        json={
            "dueDate": due_date,
            "kind": "payment",
            "transactionId": str(refund_txn),
            "closes": False,
        },
        headers=ORIGIN,
    )
    assert part_payment.status_code == 201, part_payment.text
    assert part_payment.json()["remainingNext"] == "600.0000"
    assert part_payment.json()["paidTowardNext"] == "400.0000"

    after_dashboard = client().get(f"/dashboard?asOf={due_date}", headers=ORIGIN).json()
    assert after_dashboard["thisMonth"]["incomeBase"] == income_before

    register_after = client().get("/register", headers=ORIGIN).json()["records"]
    planned_view = next(r for r in register_after if r["id"] == claim["id"])["plannedCommitment"]
    assert planned_view["remainingNext"] == "600.0000"
    assert planned_view["paidTowardNext"] == "400.0000"

    income_txn = _raw_txn(live, live.asset_account_id, "600.0000", due_date, txn_type="income")
    income_refused = client().post(
        f"/commitments/{commitment_id}/resolutions",
        json={
            "dueDate": due_date,
            "kind": "payment",
            "transactionId": str(income_txn),
            "closes": True,
        },
        headers=ORIGIN,
    )
    assert income_refused.status_code == 422, income_refused.text
    assert income_refused.json()["detail"] == "this posting cannot settle this commitment"


# ---------------------------------------------------------------------------------------
# Plan premium: cadence, sign, currency and any active account (a card included).


def test_plan_premium_rules(live: RCFixture) -> None:
    no_premium = _register("insurance_policy", title="Synthetic bare policy", keyDate=_d(10)).json()
    refused = _commitment(
        accountId=str(live.asset_account_id),
        amount="-50.0000",
        currency=live.currency,
        cadence="monthly",
        firstDate=no_premium["keyDate"],
        sourceRegisterRecordId=no_premium["id"],
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "this policy has no premium"

    policy = _register(
        "insurance_policy",
        title="Synthetic policy",
        keyDate=_d(10),
        premiumAmount="100.0000",
        premiumCadence="monthly",
        currency=live.currency,
    ).json()

    wrong_cadence = _commitment(
        accountId=str(live.asset_account_id),
        amount="-100.0000",
        currency=live.currency,
        cadence="annual",
        firstDate=policy["keyDate"],
        sourceRegisterRecordId=policy["id"],
    )
    assert wrong_cadence.status_code == 422, wrong_cadence.text

    positive = _commitment(
        accountId=str(live.asset_account_id),
        amount="100.0000",
        currency=live.currency,
        cadence="monthly",
        firstDate=policy["keyDate"],
        sourceRegisterRecordId=policy["id"],
    )
    assert positive.status_code == 422, positive.text

    wrong_currency = _commitment(
        accountId=str(live.asset_account_id),
        amount="-100.0000",
        currency=live.secondary_currency,
        cadence="monthly",
        firstDate=policy["keyDate"],
        sourceRegisterRecordId=policy["id"],
    )
    assert wrong_currency.status_code == 422, wrong_currency.text

    # A card is an eligible paying account for a policy premium.
    on_card = _commitment(
        accountId=str(live.card_account_id),
        amount="-100.0000",
        currency=live.currency,
        cadence="monthly",
        firstDate=policy["keyDate"],
        sourceRegisterRecordId=policy["id"],
    )
    assert on_card.status_code == 201, on_card.text
    assert on_card.json()["source"] == "register_record"

    second_plan = _commitment(
        accountId=str(live.asset_account_id),
        amount="-100.0000",
        currency=live.currency,
        cadence="monthly",
        firstDate=policy["keyDate"],
        sourceRegisterRecordId=policy["id"],
    )
    assert second_plan.status_code == 409, second_plan.text
    assert second_plan.json()["detail"] == "money is already planned for this record"


# ---------------------------------------------------------------------------------------
# Plan proceeds: expected proceeds required, once, positive, an active asset in the record's
# currency and not the deposit account itself; the maturity item disappears once planned.


def test_plan_proceeds_rules_and_maturity_item_disappears(live: RCFixture) -> None:
    no_proceeds = _register(
        "deposit",
        title="Synthetic bare deposit",
        keyDate=_d(10),
        accountId=str(live.deposit_account_id),
    ).json()
    refused = _commitment(
        accountId=str(live.asset_account_id),
        amount="1000.0000",
        currency=live.currency,
        cadence="once",
        firstDate=no_proceeds["keyDate"],
        sourceRegisterRecordId=no_proceeds["id"],
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "this deposit has no expected proceeds"

    deposit = _register(
        "deposit",
        title="Synthetic deposit",
        keyDate=_d(10),
        accountId=str(live.deposit_account_id),
        counterparty="Synthetic bank",
        amount="5000.0000",
        currency=live.currency,
    ).json()

    before = client().get("/actions", headers=ORIGIN).json()
    maturity_key = f"record.{deposit['id']}.maturity.{deposit['keyDate']}"
    assert maturity_key in {i["actionKey"] for i in before["items"]}

    own_account = _commitment(
        accountId=str(live.deposit_account_id),
        amount="5000.0000",
        currency=live.currency,
        cadence="once",
        firstDate=deposit["keyDate"],
        sourceRegisterRecordId=deposit["id"],
    )
    assert own_account.status_code == 422, own_account.text
    assert own_account.json()["detail"] == "choose the account that receives the proceeds"

    not_asset = _commitment(
        accountId=str(live.card_account_id),
        amount="5000.0000",
        currency=live.currency,
        cadence="once",
        firstDate=deposit["keyDate"],
        sourceRegisterRecordId=deposit["id"],
    )
    assert not_asset.status_code == 422, not_asset.text

    recurring_cadence = _commitment(
        accountId=str(live.asset_account_id),
        amount="5000.0000",
        currency=live.currency,
        cadence="monthly",
        firstDate=deposit["keyDate"],
        sourceRegisterRecordId=deposit["id"],
    )
    assert recurring_cadence.status_code == 422, recurring_cadence.text

    planned = _commitment(
        accountId=str(live.asset_account_id),
        amount="5000.0000",
        currency=live.currency,
        cadence="once",
        firstDate=deposit["keyDate"],
        sourceRegisterRecordId=deposit["id"],
    )
    assert planned.status_code == 201, planned.text
    commitment_id = planned.json()["id"]
    due_date = planned.json()["nextDueDate"]

    after = client().get("/actions", headers=ORIGIN).json()
    assert maturity_key not in {i["actionKey"] for i in after["items"]}

    # Settled against a transfer-in leg — a real cash movement, not income.
    transfer_txn = _raw_txn(live, live.asset_account_id, "5000.0000", due_date, "transfer_in")
    settle = client().post(
        f"/commitments/{commitment_id}/resolutions",
        json={
            "dueDate": due_date,
            "kind": "payment",
            "transactionId": str(transfer_txn),
            "closes": True,
        },
        headers=ORIGIN,
    )
    assert settle.status_code == 201, settle.text
    assert settle.json()["status"] == "finished"


# ---------------------------------------------------------------------------------------
# Claim rules beyond P09: a completed claim is 409; nominee/administration cannot plan money.


def test_claim_completed_and_unplannable_kinds(live: RCFixture) -> None:
    claim = _register(
        "claim",
        title="Synthetic completable claim",
        counterparty="Synthetic insurer",
        amount="500.0000",
        currency=live.currency,
        keyDate=_d(-1),
    ).json()
    completed = client().post(f"/register/{claim['id']}/complete", headers=ORIGIN)
    assert completed.status_code == 200, completed.text

    refused = _commitment(
        accountId=str(live.asset_account_id),
        amount="500.0000",
        currency=live.currency,
        cadence="once",
        firstDate=_d(0),
        sourceRegisterRecordId=claim["id"],
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "a completed claim cannot plan money"

    nominee = _register(
        "nominee",
        title="Synthetic nominee",
        accountId=str(live.asset_account_id),
        nomineeRecorded=True,
    ).json()
    nominee_refused = _commitment(
        accountId=str(live.asset_account_id),
        amount="10.0000",
        currency=live.currency,
        cadence="once",
        firstDate=_d(0),
        sourceRegisterRecordId=nominee["id"],
    )
    assert nominee_refused.status_code == 422, nominee_refused.text
    assert nominee_refused.json()["detail"] == "this record does not plan money"

    administration = _register("administration", title="Synthetic deadline", keyDate=_d(5)).json()
    administration_refused = _commitment(
        accountId=str(live.asset_account_id),
        amount="10.0000",
        currency=live.currency,
        cadence="once",
        firstDate=_d(0),
        sourceRegisterRecordId=administration["id"],
    )
    assert administration_refused.status_code == 422, administration_refused.text


def test_archived_record_cannot_plan_money(live: RCFixture) -> None:
    administration = _register(
        "administration", title="Synthetic archivable deadline", keyDate=_d(5)
    ).json()
    archived = client().post(f"/register/{administration['id']}/archive", headers=ORIGIN)
    assert archived.status_code == 200, archived.text

    refused = _commitment(
        accountId=str(live.asset_account_id),
        amount="10.0000",
        currency=live.currency,
        cadence="once",
        firstDate=_d(0),
        sourceRegisterRecordId=administration["id"],
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "an archived register record cannot plan money"


def test_two_sources_is_refused(live: RCFixture) -> None:
    administration = _register(
        "administration", title="Synthetic two-source deadline", keyDate=_d(5)
    ).json()
    refused = _commitment(
        accountId=str(live.asset_account_id),
        amount="-10.0000",
        currency=live.currency,
        cadence="once",
        firstDate=_d(0),
        sourceRegisterRecordId=administration["id"],
        sourceCardStatementId=str(uuid4()),
    )
    assert refused.status_code == 422, refused.text


# ---------------------------------------------------------------------------------------
# Household isolation with a real foreign row.


def test_household_isolation_with_a_real_foreign_row(live: RCFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign_claim = _register(
        "claim",
        title="Foreign synthetic claim",
        counterparty="Foreign insurer",
        amount="200.0000",
        currency=live.currency,
        keyDate=_d(0),
    ).json()
    foreign_planned = _commitment(
        accountId=str(live.other_account_id),
        amount="200.0000",
        currency=live.currency,
        cadence="once",
        firstDate=foreign_claim["keyDate"],
        sourceRegisterRecordId=foreign_claim["id"],
    )
    assert foreign_planned.status_code == 201, foreign_planned.text
    app.dependency_overrides[auth.require_owner] = lambda: live.owner

    mine = client().get("/commitments", headers=ORIGIN).json()
    assert foreign_planned.json()["id"] not in {c["id"] for c in mine["commitments"]}

    not_found = _commitment(
        accountId=str(live.asset_account_id),
        amount="200.0000",
        currency=live.currency,
        cadence="once",
        firstDate=foreign_claim["keyDate"],
        sourceRegisterRecordId=foreign_claim["id"],
    )
    assert not_found.status_code == 404, not_found.text

    with live.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM commitment WHERE household_id = %s", (live.other_household_id,)
        )
        row = cur.fetchone()
        assert row is not None and row[0] == 1
