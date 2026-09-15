"""Live owner-scoped tests for the Phase 3.2 Task 1 cash forecast (spec "Forecast computation"
rules 1-9). Skipped when no database is reachable. Every name, label and amount is synthetic;
dates are fixed absolute literals (not `date.today()`), same convention as `test_plans_live.py`,
since none of these rules cares about the real today.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
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
AS_OF = date(2026, 3, 15)


def client() -> TestClient:
    return TestClient(app)


@dataclass(frozen=True)
class ForecastFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    household_id: UUID
    person_id: UUID
    account_id: UUID
    funding_account_id: UUID
    card_account_id: UUID
    currency: str
    secondary_currency: str


def _account(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    person_id: UUID,
    name: str,
    currency: str,
    *,
    account_type: str = "savings",
    is_liquid: bool = True,
    credit_limit: str | None = None,
    opening: str = "10000.0000",
    # 14 days before AS_OF (2026-03-15) — not stale by default; a test that wants an aged
    # balance calls _set_balance explicitly.
    opening_date: date = date(2026, 3, 1),
    active: bool = True,
    no_balance_date: bool = False,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date, is_active,
                                 closed_on, is_liquid, credit_limit)
           VALUES (%s, %s, %s, %s, %s, 'XZ', %s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            household_id,
            person_id,
            name,
            account_type,
            currency,
            Decimal(opening),
            opening_date,
            active,
            None if active else date(2026, 2, 1),
            is_liquid,
            Decimal(credit_limit) if credit_limit is not None else None,
        ),
    )
    found = cur.fetchone()
    assert found is not None
    account_id = UUID(str(found[0]))
    if no_balance_date:
        # Rule "no_balance_date": an account whose ledger balance was never dated is excluded
        # from the forecast while it would otherwise be a currency member.
        cur.execute("UPDATE account SET current_balance_as_of = NULL WHERE id = %s", (account_id,))
    return account_id


def _set_balance(cur: psycopg.Cursor[Any], account_id: UUID, balance: str, as_of: date) -> None:
    cur.execute(
        "UPDATE account SET current_balance = %s, current_balance_as_of = %s WHERE id = %s",
        (Decimal(balance), as_of, account_id),
    )


def _card_statement(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    currency: str,
    *,
    balance: str = "500.0000",
    statement_date: date = date(2026, 2, 1),
    due_date: date = date(2026, 3, 5),
) -> UUID:
    period_end = statement_date - timedelta(days=1)
    cur.execute(
        """INSERT INTO card_statement (household_id, account_id, period_start, period_end,
                   statement_date, payment_due_date, currency, opening_balance,
                   closing_balance, statement_balance, is_paid_in_full)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, false)
           RETURNING id""",
        (
            household_id,
            account_id,
            period_end.replace(day=1),
            period_end,
            statement_date,
            due_date,
            currency,
            -Decimal(balance),
            Decimal(balance),
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _commitment_body(
    fixture: ForecastFixture, account_id: UUID, currency: str, **overrides: object
) -> dict[str, object]:
    body: dict[str, object] = {
        "accountId": str(account_id),
        "label": "Synthetic forecast commitment",
        "amount": "-500.0000",
        "currency": currency,
        "cadence": "once",
        "firstDate": "2026-03-05",
        "endDate": None,
        "nextDueDate": "2026-03-05",
        "idempotencyKey": str(uuid4()),
    }
    body.update(overrides)
    return body


def _create_commitment(fixture: ForecastFixture, **overrides: object) -> dict[str, Any]:
    body = _commitment_body(fixture, fixture.account_id, fixture.currency, **overrides)
    response = client().post("/commitments", json=body, headers=ORIGIN)
    assert response.status_code == 201, response.text
    row: dict[str, Any] = response.json()
    return row


def _forecast(as_of: date = AS_OF) -> dict[str, Any]:
    response = client().get(f"/forecast?asOf={as_of.isoformat()}", headers=ORIGIN)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _account_row(forecast: dict[str, Any], account_id: UUID) -> dict[str, Any]:
    for row in forecast["accounts"]:
        if row["accountId"] == str(account_id):
            return cast(dict[str, Any], row)
    raise AssertionError(f"{account_id} not in forecast accounts")


def _currency_row(forecast: dict[str, Any], currency: str) -> dict[str, Any] | None:
    for row in forecast["currencies"]:
        if row["currency"] == currency:
            return cast(dict[str, Any], row)
    return None


@pytest.fixture
def live() -> Iterator[ForecastFixture]:
    connection = connect_to_test_database()
    household_id = uuid4()
    person_id = uuid4()
    user_id = uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "forecast-owner@example.invalid")
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = found
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
               VALUES (%s, %s, %s, %s)""",
            (household_id, "forecast household", base, secondary),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, %s, true)""",
            (person_id, household_id, "forecast owner"),
        )
        cur.execute(
            """INSERT INTO app_user (id, household_id, person_id, email, role)
               VALUES (%s, %s, %s, %s, 'owner')""",
            (user_id, household_id, person_id, owner.email),
        )
        account_id = _account(cur, household_id, person_id, "Synthetic paying account", base)
        funding_account_id = _account(
            cur, household_id, person_id, "Synthetic funding account", base, opening="2000.0000"
        )
        card_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic forecast card",
            base,
            account_type="credit_card",
            credit_limit="5000.0000",
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield ForecastFixture(
            connection=connection,
            owner=owner,
            household_id=household_id,
            person_id=person_id,
            account_id=account_id,
            funding_account_id=funding_account_id,
            card_account_id=card_account_id,
            currency=str(base).strip(),
            secondary_currency=str(secondary).strip(),
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute(
                "TRUNCATE commitment, action_mark, card_statement, transaction, account CASCADE"
            )
        connection.commit()
        connection.close()


# --- Rule 1: card settlement mirrors (spec scenario P05) ---------------------------------------


def test_p05_card_settlement_mirrors_onto_the_card_and_leaves_the_currency_total_unchanged(
    live: ForecastFixture,
) -> None:
    with live.connection.cursor() as cur:
        # The card's own balance is dated well before the payment (2026-01-15 < 2026-03-05), so
        # the mirror applies.
        _set_balance(cur, live.card_account_id, "500.0000", date(2026, 1, 15))
    live.connection.commit()
    statement_id: UUID
    with live.connection.cursor() as cur:
        statement_id = _card_statement(
            cur, live.household_id, live.card_account_id, live.currency, balance="500.0000"
        )
    live.connection.commit()

    created = _create_commitment(live, amount="-500.0000", sourceCardStatementId=str(statement_id))
    commitment_id = created["id"]

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    card = _account_row(forecast, live.card_account_id)

    paying_occ = [o for o in paying["occurrences"] if o["commitmentId"] == commitment_id]
    assert len(paying_occ) == 1
    assert paying_occ[0]["amount"] == "-500.0000"
    assert paying_occ[0]["kind"] == "commitment"

    card_occ = [o for o in card["occurrences"] if o["commitmentId"] == commitment_id]
    assert len(card_occ) == 1
    assert card_occ[0]["amount"] == "500.0000"
    assert card_occ[0]["kind"] == "card_settlement"
    assert card_occ[0]["date"] == paying_occ[0]["date"] == "2026-03-05"

    assert not any(e["commitmentId"] == commitment_id for e in forecast["excludedCommitments"])

    # The currency's 90-day point is unaffected: the paying side and the mirror net to zero, so
    # the currency total is still just the sum of every member's own start balance.
    currency_row = _currency_row(forecast, live.currency)
    assert currency_row is not None
    member_start_sum = sum(
        Decimal(row["startBalance"])
        for row in forecast["accounts"]
        if row["accountId"] in currency_row["accountIds"]
    )
    point_90 = next(p for p in currency_row["points"] if p["days"] == 90)
    assert Decimal(point_90["balance"]) == member_start_sum


def test_p05_no_mirror_when_the_cards_own_balance_date_is_on_or_after_the_payment(
    live: ForecastFixture,
) -> None:
    with live.connection.cursor() as cur:
        # The card's own balance is dated on the payment date itself: already reflected, no
        # mirror.
        _set_balance(cur, live.card_account_id, "500.0000", date(2026, 3, 5))
    live.connection.commit()
    statement_id: UUID
    with live.connection.cursor() as cur:
        statement_id = _card_statement(
            cur, live.household_id, live.card_account_id, live.currency, balance="500.0000"
        )
    live.connection.commit()

    created = _create_commitment(live, amount="-500.0000", sourceCardStatementId=str(statement_id))
    commitment_id = created["id"]

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    card = _account_row(forecast, live.card_account_id)

    assert any(o["commitmentId"] == commitment_id for o in paying["occurrences"])
    assert not any(o["commitmentId"] == commitment_id for o in card["occurrences"])
    excluded = [e for e in forecast["excludedCommitments"] if e["commitmentId"] == commitment_id]
    assert len(excluded) == 1
    assert excluded[0]["reason"] == "card_settlement_not_projected"


def test_p05_linking_the_payment_removes_both_the_paying_and_the_mirror_occurrence(
    live: ForecastFixture,
) -> None:
    with live.connection.cursor() as cur:
        _set_balance(cur, live.card_account_id, "500.0000", date(2026, 1, 15))
    live.connection.commit()
    statement_id: UUID
    with live.connection.cursor() as cur:
        statement_id = _card_statement(
            cur, live.household_id, live.card_account_id, live.currency, balance="500.0000"
        )
    live.connection.commit()

    created = _create_commitment(live, amount="-500.0000", sourceCardStatementId=str(statement_id))
    commitment_id = created["id"]

    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                       amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                       amount_base, amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, %s, 'SYNTHETIC FORECAST PAYMENT', %s, %s, %s, 1, 1, %s, %s,
                       'expense', 'manual', %s)
               RETURNING id""",
            (
                live.household_id,
                live.account_id,
                "2026-03-06",
                Decimal("-500.0000"),
                live.currency,
                "2026-03-06",
                Decimal("-500.0000"),
                Decimal("-500.0000"),
                f"forecast-{uuid4()}",
            ),
        )
        row = cur.fetchone()
        assert row is not None
        txn_id = UUID(str(row[0]))
    live.connection.commit()

    settle = client().post(
        f"/commitments/{commitment_id}/resolutions",
        json={
            "dueDate": "2026-03-05",
            "kind": "payment",
            "transactionId": str(txn_id),
            "closes": True,
        },
        headers=ORIGIN,
    )
    assert settle.status_code == 201, settle.text

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    card = _account_row(forecast, live.card_account_id)
    assert not any(o["commitmentId"] == commitment_id for o in paying["occurrences"])
    assert not any(o["commitmentId"] == commitment_id for o in card["occurrences"])


def test_card_account_excluded_still_applies_the_paying_side(live: ForecastFixture) -> None:
    with live.connection.cursor() as cur:
        cur.execute(
            "UPDATE account SET is_active = false, closed_on = %s WHERE id = %s",
            (date(2026, 2, 1), live.card_account_id),
        )
    live.connection.commit()
    statement_id: UUID
    with live.connection.cursor() as cur:
        statement_id = _card_statement(
            cur, live.household_id, live.card_account_id, live.currency, balance="500.0000"
        )
    live.connection.commit()

    created = _create_commitment(live, amount="-500.0000", sourceCardStatementId=str(statement_id))
    commitment_id = created["id"]

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    assert any(o["commitmentId"] == commitment_id for o in paying["occurrences"])
    excluded = [e for e in forecast["excludedCommitments"] if e["commitmentId"] == commitment_id]
    assert len(excluded) == 1
    assert excluded[0]["reason"] == "card_settlement_not_projected"
    assert not any(row["accountId"] == str(live.card_account_id) for row in forecast["accounts"])


def test_card_account_in_another_currency_is_not_projected_but_the_paying_side_applies(
    live: ForecastFixture,
) -> None:
    with live.connection.cursor() as cur:
        _set_balance(cur, live.card_account_id, "500.0000", date(2026, 1, 15))
        statement_id = _card_statement(
            cur, live.household_id, live.card_account_id, live.currency, balance="500.0000"
        )
    live.connection.commit()

    created = _create_commitment(live, amount="-500.0000", sourceCardStatementId=str(statement_id))
    commitment_id = created["id"]

    # Simulate the card account's own currency drifting after the statement and commitment were
    # created (the mirror check re-reads the account's live currency, not the statement's).
    with live.connection.cursor() as cur:
        cur.execute(
            "UPDATE account SET currency = %s WHERE id = %s",
            (live.secondary_currency, live.card_account_id),
        )
    live.connection.commit()

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    assert any(o["commitmentId"] == commitment_id for o in paying["occurrences"])
    excluded = [e for e in forecast["excludedCommitments"] if e["commitmentId"] == commitment_id]
    assert len(excluded) == 1
    assert excluded[0]["reason"] == "card_settlement_not_projected"


# --- Rule 5: shortfall and funding (spec scenario P06) -----------------------------------------


def test_p06_shortfall_names_the_funding_account_and_leaves_it_unchanged(
    live: ForecastFixture,
) -> None:
    with live.connection.cursor() as cur:
        _set_balance(cur, live.account_id, "1000.0000", date(2026, 1, 1))
        # Each could cover 500 on its own but is not a funding candidate: illiquid, or another
        # currency (the liability card in the fixture is the third negative case).
        _account(
            cur,
            live.household_id,
            live.person_id,
            "Synthetic illiquid",
            live.currency,
            is_liquid=False,
            opening="5000.0000",
        )
        _account(
            cur,
            live.household_id,
            live.person_id,
            "Synthetic other currency",
            live.secondary_currency,
            opening="5000.0000",
        )
    live.connection.commit()

    outflow = _create_commitment(
        live,
        label="Synthetic outflow",
        amount="-1500.0000",
        firstDate="2026-02-01",
        nextDueDate="2026-02-01",
    )
    _create_commitment(
        live,
        label="Synthetic salary",
        amount="2000.0000",
        firstDate="2026-02-15",
        nextDueDate="2026-02-15",
    )

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    assert paying["lowest"] == {"balance": "-500.0000", "date": "2026-02-01"}
    assert paying["shortfall"] is not None
    assert paying["shortfall"]["amount"] == "500.0000"
    assert paying["shortfall"]["date"] == "2026-02-01"
    funding = paying["shortfall"]["fundingAccounts"]
    assert len(funding) == 1
    assert funding[0]["accountId"] == str(live.funding_account_id)
    assert funding[0]["lowestBalance"] == "2000.0000"

    funding_row = _account_row(forecast, live.funding_account_id)
    assert funding_row["startBalance"] == "2000.0000"
    assert funding_row["occurrences"] == []
    assert funding_row["shortfall"] is None
    assert outflow["id"] != funding_row["accountId"]  # sanity: distinct rows


# --- Rules 3, 4 and 7: confirmed-only figures and the per-currency fold -------------------------


def test_confirmed_figures_and_currency_fold_pre_as_of_occurrences_into_an_as_of_start(
    live: ForecastFixture,
) -> None:
    with live.connection.cursor() as cur:
        _set_balance(cur, live.account_id, "1000.0000", date(2026, 3, 1))
        _set_balance(cur, live.card_account_id, "300.0000", date(2026, 3, 1))
    live.connection.commit()
    # Funding keeps its 2000.0000 opening balance dated 2026-03-01.
    for label, amount, when, certainty in (
        ("Synthetic before asOf", "-400.0000", "2026-03-10", "confirmed"),
        ("Synthetic estimated", "-900.0000", "2026-04-01", "estimated"),
        ("Synthetic confirmed", "250.0000", "2026-04-20", "confirmed"),
    ):
        _create_commitment(
            live, label=label, amount=amount, firstDate=when, nextDueDate=when, certainty=certainty
        )

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    assert [(p["balance"], p["confirmedBalance"]) for p in paying["points"]] == [
        ("-300.0000", "600.0000"),
        ("-50.0000", "850.0000"),
        ("-50.0000", "850.0000"),
    ]
    assert paying["lowest"] == {"balance": "-300.0000", "date": "2026-04-01"}
    assert paying["lowestConfirmed"] == {"balance": "600.0000", "date": "2026-03-10"}

    currency_row = _currency_row(forecast, live.currency)
    assert currency_row is not None
    # 1000 + 2000 + 300 with the 2026-03-10 outflow folded into the start dated asOf.
    assert currency_row["startBalance"] == "2900.0000"
    assert [(p["balance"], p["confirmedBalance"]) for p in currency_row["points"]] == [
        ("2000.0000", "2900.0000"),
        ("2250.0000", "3150.0000"),
        ("2250.0000", "3150.0000"),
    ]
    assert currency_row["lowest"] == {"balance": "2000.0000", "date": "2026-04-01"}
    assert currency_row["lowestConfirmed"] == {"balance": "2900.0000", "date": AS_OF.isoformat()}


def test_another_households_accounts_and_card_dues_are_invisible(live: ForecastFixture) -> None:
    foreign_household, foreign_person = uuid4(), uuid4()
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
               VALUES (%s, 'foreign forecast household', %s, %s)""",
            (foreign_household, live.currency, live.secondary_currency),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, 'foreign forecast owner', true)""",
            (foreign_person, foreign_household),
        )
        foreign_ids = {
            _account(cur, foreign_household, foreign_person, "Foreign savings", live.currency),
            _account(
                cur,
                foreign_household,
                foreign_person,
                "Foreign undated",
                live.currency,
                no_balance_date=True,
            ),
        }
        foreign_card = _account(
            cur,
            foreign_household,
            foreign_person,
            "Foreign card",
            live.currency,
            account_type="credit_card",
            credit_limit="5000.0000",
        )
        foreign_ids.add(foreign_card)
        foreign_statement = _card_statement(
            cur, foreign_household, foreign_card, live.currency, due_date=date(2026, 4, 1)
        )
    live.connection.commit()

    forecast = _forecast()
    seen = {row["accountId"] for row in forecast["accounts"]}
    seen |= {row["accountId"] for row in forecast["excludedAccounts"]}
    for currency_row in forecast["currencies"]:
        seen |= set(currency_row["accountIds"]) | set(currency_row["excludedAccountIds"])
    assert not seen & {str(foreign_id) for foreign_id in foreign_ids}
    assert str(foreign_statement) not in {
        d["cardStatementId"] for d in forecast["unplannedCardDues"]
    }


# --- Rule 2: unapplied occurrences, capped -------------------------------------------------


def test_unapplied_occurrences_are_capped_at_twelve_per_commitment(live: ForecastFixture) -> None:
    with live.connection.cursor() as cur:
        # The account has not been reconciled in over a year; a monthly commitment due since
        # 2025-01-01 accumulates far more than 12 unapplied occurrences by the balance date.
        _set_balance(cur, live.account_id, "10000.0000", date(2026, 3, 1))
    live.connection.commit()

    _create_commitment(
        live,
        label="Synthetic overdue monthly",
        amount="-100.0000",
        cadence="monthly",
        firstDate="2025-01-01",
        nextDueDate="2025-01-01",
    )

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    assert len(paying["unappliedOccurrences"]) == 12
    assert all(o["dueByAsOf"] for o in paying["unappliedOccurrences"])
    dates = [o["date"] for o in paying["unappliedOccurrences"]]
    assert dates == sorted(dates)


# --- Rule 6/7: balance age, coverage gaps, currency membership ---------------------------------


def test_balance_age_coverage_gap_and_currency_membership(live: ForecastFixture) -> None:
    with live.connection.cursor() as cur:
        # More than 35 days before AS_OF: stale.
        _set_balance(cur, live.account_id, "10000.0000", date(2026, 1, 1))
        illiquid_id = _account(
            cur,
            live.household_id,
            live.person_id,
            "Synthetic illiquid asset",
            live.currency,
            is_liquid=False,
        )
        no_date_id = _account(
            cur,
            live.household_id,
            live.person_id,
            "Synthetic no balance date",
            live.currency,
            no_balance_date=True,
        )
    live.connection.commit()

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    assert paying["balanceAgeDays"] == (AS_OF - date(2026, 1, 1)).days
    assert paying["balanceAgeDays"] > 35
    # No import_batch rows exist for this 'savings' account: of the three trailing months
    # (Jan/Feb/Mar 2026), Jan and Feb are "missing" and Mar (AS_OF's own month) is "in_progress"
    # rather than a gap — so 2, not 3.
    assert paying["coverageGapMonths"] == 2

    excluded_reasons = {e["accountId"]: e["reason"] for e in forecast["excludedAccounts"]}
    assert excluded_reasons[str(no_date_id)] == "no_balance_date"

    currency_row = _currency_row(forecast, live.currency)
    assert currency_row is not None
    assert str(live.account_id) in currency_row["accountIds"]
    assert str(live.card_account_id) in currency_row["accountIds"]
    assert str(illiquid_id) not in currency_row["accountIds"]
    assert str(no_date_id) in currency_row["excludedAccountIds"]
    assert str(live.account_id) in currency_row["staleAccountIds"]
    assert str(live.funding_account_id) not in currency_row["staleAccountIds"]


# --- Rule 8: unplanned card dues, marks ignored -------------------------------------------------


def test_unplanned_card_dues_ignore_snooze_and_dismissal_marks(live: ForecastFixture) -> None:
    with live.connection.cursor() as cur:
        statement_id = _card_statement(
            cur,
            live.household_id,
            live.card_account_id,
            live.currency,
            balance="300.0000",
            due_date=date(2026, 4, 1),
        )
    live.connection.commit()

    snoozed = client().put(
        f"/actions/marks/card.{statement_id}",
        json={"until": (date.today() + timedelta(days=30)).isoformat()},
        headers=ORIGIN,
    )
    assert snoozed.status_code == 204, snoozed.text

    forecast = _forecast()
    dues = [d["cardStatementId"] for d in forecast["unplannedCardDues"]]
    assert str(statement_id) in dues

    dismissed = client().put(
        f"/actions/marks/card.{statement_id}", json={"reason": "synthetic reason"}, headers=ORIGIN
    )
    assert dismissed.status_code == 204, dismissed.text

    forecast_after_dismiss = _forecast()
    dues_after = [d["cardStatementId"] for d in forecast_after_dismiss["unplannedCardDues"]]
    assert str(statement_id) in dues_after


def test_unplanned_card_due_outside_the_window_is_excluded(live: ForecastFixture) -> None:
    with live.connection.cursor() as cur:
        statement_id = _card_statement(
            cur,
            live.household_id,
            live.card_account_id,
            live.currency,
            balance="300.0000",
            due_date=AS_OF + timedelta(days=91),
        )
    live.connection.commit()

    forecast = _forecast()
    dues = [d["cardStatementId"] for d in forecast["unplannedCardDues"]]
    assert str(statement_id) not in dues


# --- Archived commitments and dismissed occurrences contribute nothing -------------------------


def test_archived_commitment_and_dismissed_occurrence_contribute_nothing(
    live: ForecastFixture,
) -> None:
    archived = _create_commitment(live, label="Synthetic archived", amount="-250.0000")
    archive_response = client().post(f"/commitments/{archived['id']}/archive", headers=ORIGIN)
    assert archive_response.status_code == 200

    dismissable = _create_commitment(
        live,
        label="Synthetic dismissable",
        amount="-75.0000",
        cadence="monthly",
        firstDate="2026-03-05",
        nextDueDate="2026-03-05",
    )
    dismiss = client().post(
        f"/commitments/{dismissable['id']}/resolutions",
        json={"dueDate": "2026-03-05", "kind": "dismissed", "reason": "synthetic reason"},
        headers=ORIGIN,
    )
    assert dismiss.status_code == 201, dismiss.text

    forecast = _forecast()
    paying = _account_row(forecast, live.account_id)
    assert not any(o["commitmentId"] == archived["id"] for o in paying["occurrences"])
    assert not any(e["commitmentId"] == archived["id"] for e in forecast["excludedCommitments"])
    dismissable_occ = [
        o
        for o in paying["occurrences"] + paying["unappliedOccurrences"]
        if o["commitmentId"] == dismissable["id"] and o["date"] == "2026-03-05"
    ]
    assert dismissable_occ == []
    # The following month's occurrence still appears.
    next_month_occ = [
        o
        for o in paying["occurrences"]
        if o["commitmentId"] == dismissable["id"] and o["date"] == "2026-04-05"
    ]
    assert len(next_month_occ) == 1


# --- The route writes nothing --------------------------------------------------------------


def test_get_forecast_changes_no_row(live: ForecastFixture) -> None:
    _create_commitment(live, label="Synthetic unchanged", amount="-10.0000")
    with live.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM commitment WHERE household_id = %s", (live.household_id,))
        before_commitments = cur.fetchone()
        cur.execute(
            "SELECT current_balance, current_balance_as_of FROM account WHERE id = %s",
            (live.account_id,),
        )
        before_balance = cur.fetchone()

    _forecast()

    with live.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM commitment WHERE household_id = %s", (live.household_id,))
        after_commitments = cur.fetchone()
        cur.execute(
            "SELECT current_balance, current_balance_as_of FROM account WHERE id = %s",
            (live.account_id,),
        )
        after_balance = cur.fetchone()

    assert before_commitments == after_commitments
    assert before_balance == after_balance
