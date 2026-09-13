from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from threading import Event
from time import monotonic, sleep
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.investments import commands, funding
from oneledger.main import app

from .live_database import (
    clear_synthetic_account_valuation_periods,
    connect_to_test_database,
)
from .live_database import test_dsn as database_dsn


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
        for household, security_id in zip(
            (household_id, other_household_id), security_ids, strict=True
        ):
            cur.execute(
                """INSERT INTO security_price_source (
                       household_id, security_id, source_key, source_version,
                       source_security_code, effective_from)
                   VALUES (%s, %s, 'owner_manual', 'v1', %s, '2026-01-01') RETURNING id""",
                (household, security_id, f"synthetic:{security_id}"),
            )
            mapping_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO price_snapshot (
                       household_id, security_id, source_mapping_id, price_date, price, currency,
                       is_accepted, accepted_at)
                   VALUES (%s, %s, %s, '2026-02-01', 10.000000, %s, true, now())""",
                (household, security_id, mapping_id, base),
            )
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
            cur.execute("TRUNCATE price_snapshot, security_price_source CASCADE")
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


def _cash_lifecycle_event(
    fixture: FundingFixture, kind: str, amount: str, *, units: str | None = None
) -> UUID:
    shapes: dict[str, dict[str, object]] = {
        "sale": {
            "units": units or "-1.00000000",
            "price": amount,
            "grossAmount": amount,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": amount,
        },
        "cash_distribution": {
            "units": None,
            "price": None,
            "grossAmount": amount,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": amount,
        },
        "charge": {
            "units": None,
            "price": None,
            "grossAmount": None,
            "charges": amount,
            "withheldTax": "0.0000",
            "netAmount": f"-{amount}",
        },
        "withheld_tax": {
            "units": None,
            "price": None,
            "grossAmount": None,
            "charges": "0.0000",
            "withheldTax": amount,
            "netAmount": f"-{amount}",
        },
    }
    response = _client().post(
        "/investment-events/cash-lifecycle",
        json={
            "accountId": str(fixture.investment_account_id),
            "securityId": str(fixture.security_id),
            "eventDate": "2026-02-01",
            "eventKind": kind,
            "currency": fixture.currency,
            "idempotencyKey": str(uuid4()),
            **shapes[kind],
        },
    )
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


def _append_lot_purchase(
    fixture: FundingFixture, *, units: str, gross: str, event_date: str = "2026-01-01"
) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, %s, 'purchase', %s, %s, %s, 0.0000, 0.0000, %s, %s, %s,
                 1.0000000000, 2.0000000000, %s, %s, 'owner_entered', 'funding_test_v1', %s,
                 NULL, NULL, NULL, NULL)""",
            (
                fixture.investment_account_id,
                fixture.security_id,
                event_date,
                Decimal(units),
                Decimal(gross) / Decimal(units),
                Decimal(gross),
                -Decimal(gross),
                fixture.currency,
                event_date,
                -Decimal(gross),
                -Decimal(gross) * 2,
                str(uuid4()),
            ),
        )
        row = cur.fetchone()
        assert row is not None
    fixture.connection.commit()
    return UUID(str(row[0]))


def _append_lot_sale(fixture: FundingFixture, *, units: str, gross: str, event_date: str) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, %s, 'sale', %s, %s, %s, 0.0000, 0.0000, %s, %s, %s,
                 1.0000000000, 2.0000000000, %s, %s, 'owner_entered', 'funding_test_v1', %s,
                 NULL, NULL, NULL, NULL)""",
            (
                fixture.investment_account_id,
                fixture.security_id,
                event_date,
                -Decimal(units),
                Decimal(gross) / Decimal(units),
                Decimal(gross),
                Decimal(gross),
                fixture.currency,
                event_date,
                Decimal(gross),
                Decimal(gross) * 2,
                str(uuid4()),
            ),
        )
        row = cur.fetchone()
        assert row is not None
    fixture.connection.commit()
    return UUID(str(row[0]))


def _append_split_without_successor(fixture: FundingFixture, *, event_date: str) -> None:
    """Insert an incomplete-lineage split directly. No operation creates one until Task 15.2."""
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT holding_id FROM investment_transaction
                 WHERE household_id = %s AND account_id = %s AND security_id = %s LIMIT 1""",
            (fixture.owner.household_id, fixture.investment_account_id, fixture.security_id),
        )
        holding_row = cur.fetchone()
        assert holding_row is not None
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'on', true)")
        # The exact unit-effect value plays no role in these lineage fixtures; any nonzero value
        # satisfies Task 15.2's investment_event_kind_fields_agree shape for a split row.
        cur.execute(
            """INSERT INTO investment_transaction (
                 household_id, account_id, holding_id, security_id, event_date, event_kind,
                 units, unit_ratio, net_amount, currency, source_kind, source_contract,
                 source_event_id
               ) VALUES (
                 %s, %s, %s, %s, %s, 'split', 1, 2, 0, %s, 'statement',
                 'funding_test_v1', 'SYNTHETIC-INCOMPLETE-SPLIT')""",
            (
                fixture.owner.household_id,
                fixture.investment_account_id,
                holding_row[0],
                fixture.security_id,
                event_date,
                fixture.currency,
            ),
        )
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'off', true)")
    fixture.connection.commit()


def _append_split_with_successor(
    fixture: FundingFixture,
    *,
    predecessor_lot_id: UUID,
    event_date: str,
    ratio: str = "2",
    units: str = "20.00000000",
    basis: str = "100.0000",
) -> tuple[UUID, UUID]:
    """Insert a split and its successor lot directly. No operation creates one until Task 15.2."""
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT holding_id FROM investment_transaction
                 WHERE household_id = %s AND account_id = %s AND security_id = %s LIMIT 1""",
            (fixture.owner.household_id, fixture.investment_account_id, fixture.security_id),
        )
        holding_row = cur.fetchone()
        assert holding_row is not None
        cur.execute(
            "SELECT acquisition_date, currency FROM acquisition_lot WHERE id = %s",
            (predecessor_lot_id,),
        )
        predecessor_row = cur.fetchone()
        assert predecessor_row is not None
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'on', true)")
        # The exact unit-effect value plays no role in these lineage fixtures; any nonzero value
        # satisfies Task 15.2's investment_event_kind_fields_agree shape for a split row.
        cur.execute(
            """INSERT INTO investment_transaction (
                 household_id, account_id, holding_id, security_id, event_date, event_kind,
                 units, unit_ratio, net_amount, currency, source_kind, source_contract,
                 source_event_id
               ) VALUES (
                 %s, %s, %s, %s, %s, 'split', 1, %s, 0, %s, 'statement', 'funding_test_v1', %s)
               RETURNING id""",
            (
                fixture.owner.household_id,
                fixture.investment_account_id,
                holding_row[0],
                fixture.security_id,
                event_date,
                Decimal(ratio),
                predecessor_row[1],
                f"SYNTHETIC-SPLIT-{uuid4()}",
            ),
        )
        split_row = cur.fetchone()
        assert split_row is not None
        split_id = UUID(str(split_row[0]))
        # The split's own AFTER INSERT trigger refreshes the holding projection, which resets the
        # append guard to 'off' at its end; re-arm it for the successor-lot insert below.
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'on', true)")
        cur.execute(
            """INSERT INTO acquisition_lot (
                 household_id, holding_id, origin_transaction_id, acquisition_date,
                 acquired_units, cost_basis_native, currency, predecessor_lot_id
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                fixture.owner.household_id,
                holding_row[0],
                split_id,
                predecessor_row[0],
                Decimal(units),
                Decimal(basis),
                predecessor_row[1],
                predecessor_lot_id,
            ),
        )
        successor_row = cur.fetchone()
        assert successor_row is not None
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'off', true)")
    fixture.connection.commit()
    return split_id, UUID(str(successor_row[0]))


def _lot_id(fixture: FundingFixture, origin_transaction_id: UUID) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM acquisition_lot WHERE origin_transaction_id = %s",
            (origin_transaction_id,),
        )
        row = cur.fetchone()
        assert row is not None
    return UUID(str(row[0]))


def _propose_lot_allocation(
    disposal_event_id: UUID, allocations: list[dict[str, str]]
) -> httpx.Response:
    return cast(
        httpx.Response,
        _client().post(
            "/investment-funding/lot-allocation-proposals",
            json={"disposalEventId": str(disposal_event_id), "allocations": allocations},
        ),
    )


def _confirm_lot_allocation(
    disposal_event_id: UUID, allocations: list[dict[str, str]], expected_state: str
) -> httpx.Response:
    return cast(
        httpx.Response,
        _client().post(
            "/investment-funding/lot-allocations",
            json={
                "disposalEventId": str(disposal_event_id),
                "allocations": allocations,
                "expectedState": expected_state,
                "idempotencyKey": str(uuid4()),
            },
        ),
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


def _wait_for_lock(connection: psycopg.Connection, query_fragment: str) -> None:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with connection.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM pg_stat_activity
                    WHERE datname = current_database() AND pid <> pg_backend_pid()
                      AND wait_event_type = 'Lock' AND query LIKE %s""",
                (f"%{query_fragment}%",),
            )
            found = cur.fetchone()
        if found is not None and found[0] > 0:
            return
        sleep(0.01)
    pytest.fail(f"database session did not wait at {query_fragment}")


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
    assert before[2] == "-90.0000"
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


def _rights_event(fixture: FundingFixture, amount: str) -> UUID:
    value = Decimal(amount)
    response = TestClient(app).post(
        "/investment-events/unit-actions",
        json={
            "kind": "rights",
            "accountId": str(fixture.investment_account_id),
            "securityId": str(fixture.security_id),
            "eventDate": "2026-02-01",
            "units": "5.00000000",
            "price": f"{value / 5:.6f}",
            "grossAmount": amount,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": f"-{amount}",
            "currency": fixture.currency,
            "idempotencyKey": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return UUID(str(response.json()["event"]["id"]))


def test_a_rights_event_is_fundable_like_a_purchase(live_funding: FundingFixture) -> None:
    """Task 15.2's rights kind must reach the same V33 funding role as a purchase."""
    event = _rights_event(live_funding, "100.0000")
    cash = _cash(live_funding, "-100.0000")
    allocations = [_allocation(event, cash, "100.0000")]

    proposed = _proposal(allocations)
    assert proposed.status_code == 200, proposed.text
    confirmed = _confirm(allocations, proposed.json()["expectedState"])

    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["allocations"][0]["role"] == "funding"
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT txn_type::text FROM transaction WHERE id = %s", (cash,))
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


def test_fixed_cash_roles_support_proceeds_income_and_many_to_many_fees(
    live_funding: FundingFixture,
) -> None:
    _event(live_funding, "20.0000")
    sale = _cash_lifecycle_event(live_funding, "sale", "3.0000")
    distribution = _cash_lifecycle_event(live_funding, "cash_distribution", "2.0000")
    charge = _cash_lifecycle_event(live_funding, "charge", "1.0000")
    tax = _cash_lifecycle_event(live_funding, "withheld_tax", "2.0000")
    sale_cash = _cash(live_funding, "3.0000", txn_type="income")
    distribution_cash = _cash(live_funding, "2.0000", txn_type="income")
    fee_cash = _cash(live_funding, "-3.0000", txn_type="fee")
    before = _flow_and_net_worth()
    assert before[:2] == ("5.0000", "3.0000")

    allocations = [
        _allocation(sale, sale_cash, "3.0000"),
        _allocation(distribution, distribution_cash, "2.0000"),
        _allocation(charge, fee_cash, "1.0000"),
        _allocation(tax, fee_cash, "2.0000"),
    ]
    proposal = _proposal(allocations)
    assert proposal.status_code == 200, proposal.text
    assert [row["role"] for row in proposal.json()["allocations"]] == [
        "proceeds",
        "cash_distribution",
        "fee",
        "fee",
    ]
    confirmed = _confirm(allocations, proposal.json()["expectedState"])
    assert confirmed.status_code == 201, confirmed.text
    assert [row["role"] for row in confirmed.json()["allocations"]] == [
        "proceeds",
        "cash_distribution",
        "fee",
        "fee",
    ]
    after = _flow_and_net_worth()
    assert after[:2] == ("0.0000", "0.0000")
    assert after[2] == before[2]
    with live_funding.connection.cursor() as cur:
        cur.execute(
            """SELECT role::text, cash.txn_type::text, count(*), sum(allocated_amount)::text
                 FROM investment_cash_allocation allocation
                 JOIN transaction cash ON cash.id = allocation.cash_transaction_id
                GROUP BY role, cash.txn_type ORDER BY role"""
        )
        assert cur.fetchall() == [
            ("cash_distribution", "investment_income", 1, "2.0000"),
            ("fee", "investment_fee", 2, "3.0000"),
            ("proceeds", "investment_sell", 1, "3.0000"),
        ]
    unlinked = _client().delete(f"/investment-funding/cash-transactions/{fee_cash}")
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["restoredTransactionType"] == "fee"
    with live_funding.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_cash_allocation WHERE cash_transaction_id = %s",
            (fee_cash,),
        )
        assert cur.fetchone() == (0,)


def test_cash_role_currency_and_event_amount_are_atomic_boundaries(
    live_funding: FundingFixture,
) -> None:
    _event(live_funding, "20.0000")
    sale = _cash_lifecycle_event(live_funding, "sale", "3.0000")
    wrong_currency = _cash(
        live_funding,
        "3.0000",
        account_id=live_funding.secondary_cash_account_id,
        currency=live_funding.secondary_currency,
    )
    too_large = _cash(live_funding, "4.0000")

    for cash, amount in ((wrong_currency, "3.0000"), (too_large, "4.0000")):
        response = _proposal([_allocation(sale, cash, amount)])
        assert response.status_code == 422
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM investment_cash_allocation")
        assert cur.fetchone() == (0,)


def test_proceeds_must_be_unlinked_before_append_only_correction(
    live_funding: FundingFixture,
) -> None:
    _event(live_funding, "20.0000")
    sale = _cash_lifecycle_event(live_funding, "sale", "3.0000")
    cash = _cash(live_funding, "3.0000", txn_type="income")
    allocations = [_allocation(sale, cash, "3.0000")]
    proposal = _proposal(allocations)
    assert _confirm(allocations, proposal.json()["expectedState"]).status_code == 201
    replacement = {
        "accountId": str(live_funding.investment_account_id),
        "securityId": str(live_funding.security_id),
        "eventDate": "2026-02-01",
        "eventKind": "sale",
        "units": "-1.00000000",
        "price": "4.000000",
        "grossAmount": "4.0000",
        "charges": "0.0000",
        "withheldTax": "0.0000",
        "netAmount": "4.0000",
        "currency": live_funding.currency,
        "idempotencyKey": str(uuid4()),
        "expectedCorrectionState": "uncorrected",
        "correctionReason": "Synthetic proceeds correction",
    }

    blocked = _client().post(
        f"/investment-events/{sale}/cash-lifecycle-corrections", json=replacement
    )
    assert blocked.status_code == 422
    assert _client().delete(f"/investment-funding/cash-transactions/{cash}").status_code == 200
    corrected = _client().post(
        f"/investment-events/{sale}/cash-lifecycle-corrections", json=replacement
    )
    assert corrected.status_code == 201, corrected.text
    with live_funding.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_cash_allocation
                   WHERE investment_transaction_id = %s),
                 (SELECT count(*) FROM investment_transaction
                   WHERE corrects_transaction_id = %s),
                 (SELECT txn_type::text FROM transaction WHERE id = %s)""",
            (sale, sale, cash),
        )
        assert cur.fetchone() == (0, 1, "income")


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
    assert before_transfer == ("50.0000", "50.0000", "10.0000")
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
    assert after_debit == ("0.0000", "50.0000", "-40.0000")
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


@pytest.mark.parametrize("winner", ["unlink", "confirmation"])
def test_unlink_and_confirmation_serialize_event_then_cash(
    live_funding: FundingFixture, winner: str
) -> None:
    event = _event(live_funding, "100.0000")
    cash_a = _cash(live_funding, "-40.0000")
    allocation_a = [_allocation(event, cash_a, "40.0000")]
    proposal_a = _proposal(allocation_a)
    assert _confirm(allocation_a, proposal_a.json()["expectedState"]).status_code == 201
    cash_b = _cash(live_funding, "-60.0000")
    allocation_b = [_allocation(event, cash_b, "60.0000")]
    proposal_b = _proposal(allocation_b)
    assert proposal_b.status_code == 200, proposal_b.text
    blocker = psycopg.connect(database_dsn())
    observer = psycopg.connect(database_dsn(), autocommit=True)
    pool = ThreadPoolExecutor(max_workers=2)
    blocker.execute("SELECT id FROM investment_transaction WHERE id = %s FOR UPDATE", (event,))
    calls = {
        "unlink": lambda: _client().delete(f"/investment-funding/cash-transactions/{cash_a}"),
        "confirmation": lambda: _confirm(allocation_b, proposal_b.json()["expectedState"]),
    }
    waits = {
        "unlink": "unlink_investment_cash_allocations",
        "confirmation": "SELECT id FROM investment_transaction WHERE household_id",
    }
    second = "confirmation" if winner == "unlink" else "unlink"
    try:
        first_result = pool.submit(calls[winner])
        _wait_for_lock(observer, waits[winner])
        second_result = pool.submit(calls[second])
        _wait_for_lock(observer, waits[second])
        blocker.commit()
        responses = {
            winner: first_result.result(timeout=5),
            second: second_result.result(timeout=5),
        }
    finally:
        blocker.rollback()
        blocker.close()
        observer.close()
        pool.shutdown(wait=True, cancel_futures=True)
    assert responses["unlink"].status_code == 200, responses["unlink"].text
    with live_funding.connection.cursor() as cur:
        cur.execute(
            """SELECT cash_transaction_id, allocated_amount::text
                 FROM investment_cash_allocation WHERE investment_transaction_id = %s""",
            (event,),
        )
        remaining = cur.fetchall()
    if winner == "unlink":
        assert responses["confirmation"].status_code == 409
        assert responses["confirmation"].json() == {"detail": "funding proposal state changed"}
        assert remaining == []
    else:
        assert responses["confirmation"].status_code == 201
        assert remaining == [(cash_b, "60.0000")]


def test_disposal_lot_allocation_proposes_and_confirms_exact_basis(
    live_funding: FundingFixture,
) -> None:
    purchase = _append_lot_purchase(live_funding, units="10.00000000", gross="100.0000")
    lot_id = _lot_id(live_funding, purchase)
    disposal = _append_lot_sale(
        live_funding, units="4.00000000", gross="40.0000", event_date="2026-02-01"
    )
    allocations = [{"acquisitionLotId": str(lot_id), "allocatedUnits": "4.00000000"}]

    proposed = _propose_lot_allocation(disposal, allocations)
    assert proposed.status_code == 200, proposed.text
    confirmed = _confirm_lot_allocation(disposal, allocations, proposed.json()["expectedState"])

    assert confirmed.status_code == 201, confirmed.text
    body = confirmed.json()
    assert body["realisedCostNative"] == "40.0000"
    assert body["realisedGainNative"] == "0.0000"
    assert body["remainingBasisNative"] == "60.0000"
    assert body["reviewReasons"] == []


def test_remaining_basis_is_unbounded_and_includes_a_later_acquisition(
    live_funding: FundingFixture,
) -> None:
    purchase = _append_lot_purchase(
        live_funding, units="10.00000000", gross="100.0000", event_date="2026-01-01"
    )
    lot_id = _lot_id(live_funding, purchase)
    # A later acquisition, created before the disposal is confirmed, must still count toward
    # remaining basis: V39's remaining-basis claim is an unbounded current-state total, not as of
    # the disposal's own date.
    _append_lot_purchase(live_funding, units="5.00000000", gross="50.0000", event_date="2026-03-01")
    disposal = _append_lot_sale(
        live_funding, units="4.00000000", gross="12.0000", event_date="2026-02-01"
    )
    allocations = [{"acquisitionLotId": str(lot_id), "allocatedUnits": "4.00000000"}]

    proposed = _propose_lot_allocation(disposal, allocations)
    assert proposed.status_code == 200, proposed.text
    confirmed = _confirm_lot_allocation(disposal, allocations, proposed.json()["expectedState"])

    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["remainingBasisNative"] == "110.0000"


def test_disposal_dated_on_or_after_an_unavailable_lineage_split_refuses_allocation(
    live_funding: FundingFixture,
) -> None:
    purchase = _append_lot_purchase(live_funding, units="10.00000000", gross="100.0000")
    lot_id = _lot_id(live_funding, purchase)
    _append_split_without_successor(live_funding, event_date="2026-02-01")
    disposal = _append_lot_sale(
        live_funding, units="3.00000000", gross="30.0000", event_date="2026-02-02"
    )
    allocations = [{"acquisitionLotId": str(lot_id), "allocatedUnits": "3.00000000"}]

    proposed = _propose_lot_allocation(disposal, allocations)

    assert proposed.status_code == 422, proposed.text
    assert proposed.json() == {"detail": "lot allocation is not eligible"}
    with live_funding.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_lot_allocation WHERE disposal_transaction_id = %s",
            (disposal,),
        )
        assert cur.fetchone() == (0,)
        cur.execute("SELECT count(*) FROM investment_lot_allocation_decision")
        assert cur.fetchone() == (0,)


def test_successor_lot_before_its_creating_event_refuses_allocation_at_proposal(
    live_funding: FundingFixture,
) -> None:
    purchase = _append_lot_purchase(live_funding, units="10.00000000", gross="100.0000")
    lot_id = _lot_id(live_funding, purchase)
    disposal = _append_lot_sale(
        live_funding, units="3.00000000", gross="9.0000", event_date="2026-01-10"
    )
    _split_id, successor_id = _append_split_with_successor(
        live_funding, predecessor_lot_id=lot_id, event_date="2026-01-20"
    )
    allocations = [{"acquisitionLotId": str(successor_id), "allocatedUnits": "3.00000000"}]

    proposed = _propose_lot_allocation(disposal, allocations)

    assert proposed.status_code == 422, proposed.text
    assert proposed.json() == {"detail": "lot allocation is not eligible"}
    with live_funding.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_lot_allocation WHERE disposal_transaction_id = %s",
            (disposal,),
        )
        assert cur.fetchone() == (0,)


def test_consumed_predecessor_refuses_allocation_at_proposal(
    live_funding: FundingFixture,
) -> None:
    purchase = _append_lot_purchase(live_funding, units="10.00000000", gross="100.0000")
    lot_id = _lot_id(live_funding, purchase)
    _append_split_with_successor(live_funding, predecessor_lot_id=lot_id, event_date="2026-02-01")
    disposal = _append_lot_sale(
        live_funding, units="3.00000000", gross="9.0000", event_date="2026-03-01"
    )
    allocations = [{"acquisitionLotId": str(lot_id), "allocatedUnits": "3.00000000"}]

    proposed = _propose_lot_allocation(disposal, allocations)

    assert proposed.status_code == 422, proposed.text
    assert proposed.json() == {"detail": "lot allocation is not eligible"}
    with live_funding.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_lot_allocation WHERE disposal_transaction_id = %s",
            (disposal,),
        )
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
