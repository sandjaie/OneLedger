from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.investments import commands, funding
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods, connect_to_test_database


@dataclass(frozen=True)
class FundingFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    investment_account_id: UUID
    cash_account_id: UUID
    second_cash_account_id: UUID
    secondary_cash_account_id: UUID
    other_cash_account_id: UUID
    security_id: UUID
    other_security_id: UUID
    currency: str
    secondary_currency: str


@pytest.fixture
def live_funding() -> Iterator[FundingFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "funding-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-funding-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = (str(value).strip() for value in currencies)
        for household, name in (
            (household_id, "funding household"),
            (other_household_id, "other funding household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES ('2026-02-01', %s, %s, 0.5000000000, 'manual')
               ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                 SET rate = EXCLUDED.rate, source = EXCLUDED.source""",
            (secondary, base),
        )
        for person, household, name in (
            (person_id, household_id, "funding owner"),
            (other_person_id, other_household_id, "other funding owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, "funding-owner@example.invalid"),
            (other_user_id, other_household_id, other_person_id, "other-owner@example.invalid"),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )

        account_ids: list[UUID] = []
        for household, person, name, account_type, currency in (
            (household_id, person_id, "Synthetic investment account", "brokerage", base),
            (household_id, person_id, "Synthetic cash account", "savings", base),
            (household_id, person_id, "Second synthetic cash account", "savings", base),
            (household_id, person_id, "Alternate cash account", "savings", secondary),
            (other_household_id, other_person_id, "Other investment", "brokerage", base),
            (other_household_id, other_person_id, "Other cash", "savings", base),
        ):
            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
                   VALUES (%s, %s, %s, %s, %s, 'XZ', 0, '2026-01-01') RETURNING id""",
                (household, person, name, account_type, currency),
            )
            found = cur.fetchone()
            assert found is not None
            account_ids.append(UUID(str(found[0])))

        security_ids: list[UUID] = []
        for household, name in (
            (household_id, "Synthetic funding security"),
            (other_household_id, "Other synthetic funding security"),
        ):
            cur.execute(
                """INSERT INTO security (
                       household_id, security_type, name, currency, country, asset_class)
                   VALUES (%s, 'mutual_fund', %s, %s, 'XZ', 'equity') RETURNING id""",
                (household, name, base),
            )
            found = cur.fetchone()
            assert found is not None
            security_ids.append(UUID(str(found[0])))
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield FundingFixture(
            connection,
            owner,
            other_owner,
            account_ids[0],
            account_ids[1],
            account_ids[2],
            account_ids[3],
            account_ids[5],
            security_ids[0],
            security_ids[1],
            base,
            secondary,
        )
    finally:
        app.dependency_overrides.clear()
        connection.rollback()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE transaction, transfer, investment_transaction CASCADE")
            cur.execute("TRUNCATE holding CASCADE")
            cur.execute(
                "DELETE FROM security WHERE household_id IN (%s, %s)",
                (household_id, other_household_id),
            )
            clear_synthetic_account_valuation_periods(cur)
            cur.execute(
                "DELETE FROM account WHERE household_id IN (%s, %s)",
                (household_id, other_household_id),
            )
            cur.execute("DELETE FROM app_user WHERE id IN (%s, %s)", (user_id, other_user_id))
            cur.execute("DELETE FROM person WHERE id IN (%s, %s)", (person_id, other_person_id))
            cur.execute(
                "DELETE FROM household WHERE id IN (%s, %s)",
                (household_id, other_household_id),
            )
            cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")
        connection.commit()
        connection.close()


def _client() -> TestClient:
    return TestClient(app)


def _event_body(fixture: FundingFixture, amount: str, kind: str = "purchase") -> dict[str, str]:
    return {
        "accountId": str(fixture.investment_account_id),
        "securityId": str(fixture.security_id),
        "eventDate": "2026-02-01",
        "eventKind": kind,
        "units": "1.00000000",
        "price": amount,
        "grossAmount": amount,
        "charges": "0.0000",
        "withheldTax": "0.0000",
        "netAmount": f"-{amount}",
        "currency": fixture.currency,
        "idempotencyKey": str(uuid4()),
    }


def _event(
    fixture: FundingFixture,
    amount: str,
    *,
    other: bool = False,
    kind: str = "purchase",
) -> UUID:
    body = _event_body(fixture, amount, kind)
    if other:
        with fixture.connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.user_id', %s, true)",
                (str(fixture.other_owner.user_id),),
            )
            cur.execute(
                """SELECT account_id FROM account_valuation_period
                     WHERE household_id = %s AND valuation_mode = 'holdings' LIMIT 1""",
                (fixture.other_owner.household_id,),
            )
            account = cur.fetchone()
            assert account is not None
            cur.execute(
                """SELECT append_investment_transaction(
                     %s, %s, '2026-02-01', 'purchase', 1.00000000, %s, %s,
                     0.0000, 0.0000, %s, %s, '2026-02-01', 1.0000000000,
                     2.0000000000, %s, %s, 'owner_entered', 'funding_test_v1', %s,
                     NULL, NULL, NULL, NULL)""",
                (
                    account[0],
                    fixture.other_security_id,
                    Decimal(amount),
                    Decimal(amount),
                    -Decimal(amount),
                    fixture.currency,
                    -Decimal(amount),
                    -Decimal(amount) * 2,
                    str(uuid4()),
                ),
            )
            found = cur.fetchone()
            assert found is not None
        fixture.connection.commit()
        return UUID(str(found[0]))
    response = _client().post("/investment-events/acquisitions", json=body)
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


def _cash(
    fixture: FundingFixture,
    amount: str,
    *,
    account_id: UUID | None = None,
    household_id: UUID | None = None,
    currency: str | None = None,
    txn_type: str = "expense",
    parent_id: UUID | None = None,
) -> UUID:
    value = Decimal(amount)
    household = household_id or fixture.owner.household_id
    account = account_id or fixture.cash_account_id
    native_currency = currency or fixture.currency
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, amount, currency,
                   fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                   amount_secondary, txn_type, source, dedupe_hash, parent_transaction_id)
               VALUES (%s, %s, '2026-02-01', 'SYNTHETIC FUNDING CASH', %s, %s,
                       '2026-02-01', 1.0000000000, 2.0000000000, %s, %s, %s,
                       'manual', %s, %s) RETURNING id""",
            (
                household,
                account,
                value,
                native_currency,
                value,
                value * 2,
                txn_type,
                uuid4().hex + uuid4().hex,
                parent_id,
            ),
        )
        found = cur.fetchone()
        assert found is not None
    fixture.connection.commit()
    return UUID(str(found[0]))


def _split_cash(fixture: FundingFixture, parent_id: UUID) -> None:
    with fixture.connection.cursor() as cur:
        for amount in (Decimal("-40.0000"), Decimal("-60.0000")):
            cur.execute(
                """INSERT INTO transaction (
                       household_id, account_id, booked_date, description_raw, amount, currency,
                       fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                       amount_secondary, txn_type, source, dedupe_hash, parent_transaction_id)
                       VALUES (%s, %s, '2026-02-01', 'SYNTHETIC FUNDING CASH', %s, %s,
                           '2026-02-01', 1.0000000000, 2.0000000000, %s, %s, 'expense',
                           'manual', %s, %s)""",
                (
                    fixture.owner.household_id,
                    fixture.cash_account_id,
                    amount,
                    fixture.currency,
                    amount,
                    amount * 2,
                    uuid4().hex + uuid4().hex,
                    parent_id,
                ),
            )
    fixture.connection.commit()


def _allocation(event_id: UUID, cash_id: UUID, amount: str) -> dict[str, str]:
    return {
        "investmentEventId": str(event_id),
        "cashTransactionId": str(cash_id),
        "allocatedAmount": amount,
    }


def _proposal(allocations: list[dict[str, str]]) -> httpx.Response:
    return cast(
        httpx.Response,
        _client().post("/investment-funding/proposals", json={"allocations": allocations}),
    )


def _confirm(allocations: list[dict[str, str]], expected_state: str) -> httpx.Response:
    return cast(
        httpx.Response,
        _client().post(
            "/investment-funding/confirmations",
            json={"allocations": allocations, "expectedState": expected_state},
        ),
    )


def _correct(fixture: FundingFixture, event_id: UUID, amount: str = "100.0000") -> httpx.Response:
    body = {
        **_event_body(fixture, amount),
        "expectedCorrectionState": "uncorrected",
        "correctionReason": "Synthetic funding correction",
    }
    return cast(
        httpx.Response,
        _client().post(f"/investment-events/{event_id}/corrections", json=body),
    )


def _flow_and_net_worth() -> tuple[str, str, str | None]:
    response = _client().get("/dashboard", params={"asOf": "2026-02-28"})
    assert response.status_code == 200, response.text
    body = response.json()
    return (
        body["thisMonth"]["incomeBase"],
        body["thisMonth"]["spendingBase"],
        body["netWorth"]["totalBase"],
    )


def test_proposal_writes_nothing_then_confirmation_and_unlink_are_exact(
    live_funding: FundingFixture,
) -> None:
    event = _event(live_funding, "100.0000")
    cash_a = _cash(live_funding, "-40.0000", txn_type="expense")
    cash_b = _cash(
        live_funding,
        "-60.0000",
        account_id=live_funding.second_cash_account_id,
        txn_type="fee",
    )
    allocations = [_allocation(event, cash_a, "40.0000"), _allocation(event, cash_b, "60.0000")]
    before = _flow_and_net_worth()
    proposed = _proposal(allocations)
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["state"] == "ready"
    assert len(proposed.json()["expectedState"]) == 64
    assert [item["allocatedAmount"] for item in proposed.json()["allocations"]] == [
        "40.0000",
        "60.0000",
    ]
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_cash_allocation")
        assert cur.fetchone() == (0,)
        cur.execute(
            "SELECT txn_type::text FROM transaction WHERE id = ANY(%s) ORDER BY id",
            ([cash_a, cash_b],),
        )
        assert sorted(row[0] for row in cur.fetchall()) == ["expense", "fee"]
    assert _flow_and_net_worth() == before
    confirmed = _confirm(allocations, proposed.json()["expectedState"])
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["state"] == "confirmed"
    assert all(item["role"] == "funding" for item in confirmed.json()["allocations"])
    after = _flow_and_net_worth()
    assert after[0] == before[0] == "0.0000"
    assert after[1] == "0.0000"
    assert after[2] == before[2]
    with live_funding.connection.cursor() as cur:
        cur.execute(
            """SELECT cash_transaction_id, prior_txn_type::text, cash.txn_type::text
                 FROM investment_cash_allocation allocation
                 JOIN transaction cash ON cash.id = allocation.cash_transaction_id
                ORDER BY cash_transaction_id"""
        )
        found = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        assert found == {
            cash_a: ("expense", "investment_buy"),
            cash_b: ("fee", "investment_buy"),
        }
    unlinked = _client().delete(f"/investment-funding/cash-transactions/{cash_b}")
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json() == {
        "state": "unlinked",
        "cashTransactionId": str(cash_b),
        "restoredTransactionType": "fee",
        "removedAllocationCount": 1,
    }
    assert _flow_and_net_worth()[1] == "60.0000"
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT txn_type::text FROM transaction WHERE id = %s", (cash_a,))
        assert cur.fetchone() == ("investment_buy",)


def test_one_cash_leaf_can_fund_many_events(live_funding: FundingFixture) -> None:
    first = _event(live_funding, "30.0000")
    second = _event(live_funding, "70.0000", kind="recurring_purchase")
    cash = _cash(live_funding, "-100.0000")
    allocations = [_allocation(first, cash, "30.0000"), _allocation(second, cash, "70.0000")]
    proposed = _proposal(allocations)
    assert proposed.status_code == 200, proposed.text
    confirmed = _confirm(allocations, proposed.json()["expectedState"])
    assert confirmed.status_code == 201, confirmed.text
    assert {item["investmentEventId"] for item in confirmed.json()["allocations"]} == {
        str(first),
        str(second),
    }
    with live_funding.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*), sum(allocated_amount)::text
                 FROM investment_cash_allocation WHERE cash_transaction_id = %s""",
            (cash,),
        )
        assert cur.fetchone() == (2, "100.0000")


@pytest.mark.parametrize(
    "case", "partial over split direction currency foreign_event foreign_cash".split()
)
def test_invalid_funding_proposals_are_fixed_atomic_refusals(
    live_funding: FundingFixture, case: str
) -> None:
    event = _event(live_funding, "100.0000")
    cash = _cash(live_funding, "-100.0000")
    amount = "100.0000"
    expected_status = 422
    if case == "partial":
        amount = "99.0000"
    elif case == "over":
        amount = "100.0000"
        event = _event(live_funding, "80.0000")
    elif case == "split":
        _split_cash(live_funding, cash)
    elif case == "direction":
        cash = _cash(live_funding, "100.0000")
    elif case == "currency":
        cash = _cash(
            live_funding,
            "-100.0000",
            account_id=live_funding.secondary_cash_account_id,
            currency=live_funding.secondary_currency,
        )
    elif case == "foreign_event":
        event = _event(live_funding, "100.0000", other=True)
        expected_status = 404
    elif case == "foreign_cash":
        cash = _cash(
            live_funding,
            "-100.0000",
            account_id=live_funding.other_cash_account_id,
            household_id=live_funding.other_owner.household_id,
        )
        expected_status = 404
    response = _proposal([_allocation(event, cash, amount)])
    assert response.status_code == expected_status
    assert response.json()["detail"] in {
        "funding reference not found",
        "funding allocation is not a full eligible cash-leaf decision",
    }
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_cash_allocation")
        assert cur.fetchone() == (0,)


def test_a_transfer_leg_is_not_the_final_funding_debit(live_funding: FundingFixture) -> None:
    event = _event(live_funding, "50.0000")
    source = _cash(live_funding, "-50.0000", account_id=live_funding.cash_account_id)
    destination = _cash(
        live_funding,
        "50.0000",
        account_id=live_funding.second_cash_account_id,
        txn_type="income",
    )
    before_transfer = _flow_and_net_worth()
    assert before_transfer[:2] == ("50.0000", "50.0000")
    transfer = _client().post(
        "/transfers",
        json={
            "sourceTransactionId": str(source),
            "destinationTransactionId": str(destination),
            "fromCandidate": False,
        },
    )
    assert transfer.status_code == 201, transfer.text
    after_transfer = _flow_and_net_worth()
    assert after_transfer[:2] == ("0.0000", "0.0000")
    assert after_transfer[2] == before_transfer[2]
    final_debit = _cash(
        live_funding,
        "-50.0000",
        account_id=live_funding.second_cash_account_id,
    )
    after_debit = _flow_and_net_worth()
    assert after_debit[:2] == ("0.0000", "50.0000")
    assert after_debit[2] != after_transfer[2]
    transfer_leg = _proposal([_allocation(event, source, "50.0000")])
    final = _proposal([_allocation(event, final_debit, "50.0000")])
    assert transfer_leg.status_code == 422
    assert transfer_leg.json() == {
        "detail": "funding allocation is not a full eligible cash-leaf decision"
    }
    assert final.status_code == 200, final.text
    confirmed = _confirm(
        [_allocation(event, final_debit, "50.0000")], final.json()["expectedState"]
    )
    assert confirmed.status_code == 201, confirmed.text
    after_confirmation = _flow_and_net_worth()
    assert after_confirmation[:2] == ("0.0000", "0.0000")
    assert after_confirmation[2] == after_debit[2]
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT txn_type::text FROM transaction WHERE id = %s", (source,))
        assert cur.fetchone() == ("transfer_out",)


def test_only_the_active_acquisition_correction_leaf_can_be_funded(
    live_funding: FundingFixture,
) -> None:
    original = _event(live_funding, "100.0000")
    corrected = _correct(live_funding, original)
    assert corrected.status_code == 201, corrected.text
    replacement = UUID(corrected.json()["id"])
    cash = _cash(live_funding, "-100.0000")
    refused = _proposal([_allocation(original, cash, "100.0000")])
    active = _proposal([_allocation(replacement, cash, "100.0000")])
    assert refused.status_code == 422
    assert refused.json() == {
        "detail": "funding allocation is not a full eligible cash-leaf decision"
    }
    assert active.status_code == 200, active.text
    confirmed = _confirm(
        [_allocation(replacement, cash, "100.0000")], active.json()["expectedState"]
    )
    assert confirmed.status_code == 201, confirmed.text


def test_a_funded_acquisition_cannot_be_corrected(live_funding: FundingFixture) -> None:
    event = _event(live_funding, "100.0000")
    cash = _cash(live_funding, "-100.0000")
    allocations = [_allocation(event, cash, "100.0000")]
    proposal = _proposal(allocations)
    assert _confirm(allocations, proposal.json()["expectedState"]).status_code == 201
    correction = _correct(live_funding, event)
    assert correction.status_code == 422
    assert correction.json() == {"detail": "a funded investment event cannot be corrected"}
    with live_funding.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_transaction
                   WHERE corrects_transaction_id = %s),
                 (SELECT count(*) FROM investment_cash_allocation
                   WHERE investment_transaction_id = %s)""",
            (event, event),
        )
        assert cur.fetchone() == (0, 1)


def test_correction_winning_confirmation_race_returns_stale_conflict(
    live_funding: FundingFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _event(live_funding, "100.0000")
    cash = _cash(live_funding, "-100.0000")
    allocations = [_allocation(event, cash, "100.0000")]
    proposal = _proposal(allocations)
    correction_locked, confirmation_started, release = Event(), Event(), Event()
    real_screen = commands._screen_correction_reason
    real_load = funding._load_state

    def pause_correction(cur: psycopg.Cursor[Any], reason: str) -> None:
        real_screen(cur, reason)
        correction_locked.set()
        assert release.wait(timeout=5)

    def mark_confirmation(
        cur: psycopg.Cursor[Any],
        owner: auth.OwnerSession,
        body: funding.FundingProposalRequest,
        *,
        lock: bool,
    ) -> funding._State:
        if lock:
            confirmation_started.set()
        return real_load(cur, owner, body, lock=lock)

    monkeypatch.setattr(commands, "_screen_correction_reason", pause_correction)
    monkeypatch.setattr(funding, "_load_state", mark_confirmation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        correcting = pool.submit(_correct, live_funding, event)
        assert correction_locked.wait(timeout=5)
        confirming = pool.submit(_confirm, allocations, proposal.json()["expectedState"])
        assert confirmation_started.wait(timeout=5)
        release.set()
        correction, confirmation = correcting.result(), confirming.result()
    assert correction.status_code == 201, correction.text
    assert confirmation.status_code == 409
    assert confirmation.json() == {"detail": "funding proposal state changed"}
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_cash_allocation")
        assert cur.fetchone() == (0,)


def test_stale_and_replayed_confirmations_are_conflicts(live_funding: FundingFixture) -> None:
    event = _event(live_funding, "20.0000")
    cash = _cash(live_funding, "-20.0000")
    allocations = [_allocation(event, cash, "20.0000")]
    proposed = _proposal(allocations)
    assert proposed.status_code == 200, proposed.text
    with live_funding.connection.cursor() as cur:
        cur.execute(
            """UPDATE transaction SET txn_type = 'fee',
                                      updated_at = updated_at + interval '1 second'
                 WHERE id = %s""",
            (cash,),
        )
    live_funding.connection.commit()
    stale = _confirm(allocations, proposed.json()["expectedState"])
    assert stale.status_code == 409
    assert stale.json() == {"detail": "funding proposal state changed"}
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_cash_allocation")
        assert cur.fetchone() == (0,)
    fresh = _proposal(allocations)
    confirmed = _confirm(allocations, fresh.json()["expectedState"])
    replayed = _confirm(allocations, fresh.json()["expectedState"])
    assert confirmed.status_code == 201, confirmed.text
    assert replayed.status_code == 409
    assert replayed.json() == {"detail": "funding proposal state changed"}


def test_cross_household_unlink_is_indistinguishable_from_missing(
    live_funding: FundingFixture,
) -> None:
    event = _event(live_funding, "20.0000")
    cash = _cash(live_funding, "-20.0000")
    allocations = [_allocation(event, cash, "20.0000")]
    proposal = _proposal(allocations)
    assert proposal.status_code == 200, proposal.text
    confirmed = _confirm(allocations, proposal.json()["expectedState"])
    assert confirmed.status_code == 201, confirmed.text
    app.dependency_overrides[auth.require_owner] = lambda: live_funding.other_owner
    hidden = _client().delete(f"/investment-funding/cash-transactions/{cash}")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "funding allocation not found"}
    with live_funding.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_cash_allocation WHERE cash_transaction_id = %s",
            (cash,),
        )
        assert cur.fetchone() == (1,)
