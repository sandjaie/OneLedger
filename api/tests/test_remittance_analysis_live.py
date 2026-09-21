"""Phase 4.4 Task 1's routes against a real database: `GET /transfers/analysis` and the
`PATCH /transfers/{id}` correction.

Two things here matter most. The analysis must never re-derive a cost — a rate corrected after
the fact cannot change a past year's stated figure (ADR-0002) — and the correction must carry the
financial write floor in `docs/development-cycle.md`: retry, precision, refusal and no partial
row.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, Fixture, _clean, _connect
from .test_transfers_live import a_leg, an_account

MARCH = date(2026, 3, 1)
LAST_MARCH = date(2025, 3, 1)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Fixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
    try:
        fixture = Fixture(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def client() -> TestClient:
    return TestClient(app)


def _not_the_owner() -> auth.OwnerSession:
    """What `require_owner` does for a session that is not this household's owner."""
    raise HTTPException(status_code=403, detail="not authorized")


def a_rate(
    live: Fixture, on: date, currency: str, rate: str, *, carried_from: date | None = None
) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source,
                                    is_carried_forward, carried_from)
               VALUES (%s, %s, %s, %s, 'manual', %s, %s)
               ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                  SET rate = EXCLUDED.rate,
                      is_carried_forward = EXCLUDED.is_carried_forward,
                      carried_from = EXCLUDED.carried_from""",
            (on, currency, live.base, Decimal(rate), carried_from is not None, carried_from),
        )
    live.conn.commit()


def a_remittance(
    live: Fixture,
    *,
    sent: str,
    received: str,
    destination_currency: str = "INR",
    provider: str | None = "Exchange house",
    spread: str | None = None,
    total: str | None = None,
    implied: str | None = None,
    market: str | None = None,
    on: date = MARCH,
    household: UUID | None = None,
    confirm_legs: bool = False,
) -> UUID:
    """One confirmed remittance and its two legs.

    The FX figures are written as `FX_COST` would have left them, because these tests are about
    what the analysis does with a confirmed transfer, not about how one is confirmed —
    `test_transfers_live.py` owns that. `confirm_legs` additionally runs `confirm_transfer()`, so
    the legs carry `transfer_id` and leave `v_transaction_reportable`.
    """
    owner = household or live.household_id
    source_account = an_account(live, household=owner)
    destination_account = an_account(live, currency=destination_currency, household=owner)
    with live.conn.cursor() as cur:
        # `an_account` anchors its opening balance at 2026-01-01, and the ledger refuses a
        # transaction before its account's anchor. A multi-year fixture needs an earlier one.
        cur.execute(
            "UPDATE account SET opening_balance_date = DATE '2024-01-01' WHERE id IN (%s, %s)",
            (source_account, destination_account),
        )
    live.conn.commit()
    debit = a_leg(live, source_account, f"-{sent}", on, "REMIT OUT", household=owner)
    credit = a_leg(
        live,
        destination_account,
        received,
        on,
        "INWARD REMIT",
        currency=destination_currency,
        to_base="1",
        household=owner,
    )
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                                     source_transaction_id, destination_transaction_id,
                                     source_amount, source_currency, destination_amount,
                                     destination_currency, transfer_date, transfer_kind,
                                     match_method, status, provider, implied_rate, market_rate,
                                     fx_spread_cost_source, total_cost_source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'remittance', 'user_confirmed',
                       'confirmed', %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                owner,
                source_account,
                destination_account,
                debit,
                credit,
                Decimal(sent),
                live.base,
                Decimal(received),
                destination_currency,
                on,
                provider,
                None if implied is None else Decimal(implied),
                None if market is None else Decimal(market),
                None if spread is None else Decimal(spread),
                None if total is None else Decimal(total),
            ),
        )
        transfer_id = cur.fetchone()[0]  # type: ignore[index]
        if confirm_legs:
            cur.execute(
                """UPDATE transaction SET transfer_id = %s,
                          txn_type = CASE WHEN amount < 0 THEN 'transfer_out'::txn_type
                                          ELSE 'transfer_in'::txn_type END
                    WHERE id IN (%s, %s)""",
                (transfer_id, debit, credit),
            )
    live.conn.commit()
    return UUID(str(transfer_id))


def analysis(**query: object) -> dict[str, Any]:
    response = client().get("/transfers/analysis", params=query, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def only_year(body: dict[str, Any]) -> dict[str, Any]:
    [corridor] = body["corridors"]
    year: dict[str, Any] = corridor["years"][0]
    return year


def stored(live: Fixture, transfer_id: UUID) -> tuple[Any, ...]:
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT provider, explicit_fee_amount, explicit_fee_currency, total_cost_source,
                      implied_rate, market_rate, fx_spread_pct, fx_spread_cost_source, notes
                 FROM transfer WHERE id = %s""",
            (transfer_id,),
        )
        found = cur.fetchone()
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# GET /transfers/analysis
# ---------------------------------------------------------------------------


def test_the_analysis_writes_nothing(live: Fixture) -> None:
    a_remittance(live, sent="5000", received="111000", spread="77.6000", total="77.6000")
    with live.conn.cursor() as cur:
        cur.execute("SELECT id, updated_at FROM transfer ORDER BY id")
        before = list(cur.fetchall())

    analysis()

    with live.conn.cursor() as cur:
        cur.execute("SELECT id, updated_at FROM transfer ORDER BY id")
        assert list(cur.fetchall()) == before


def test_each_corridor_holds_only_its_own_currency_pair(live: Fixture) -> None:
    a_remittance(live, sent="5000", received="111000", destination_currency="INR")
    a_remittance(live, sent="4000", received="1080", destination_currency="USD")

    body = analysis()

    pairs = [(c["sourceCurrency"], c["destinationCurrency"]) for c in body["corridors"]]
    assert pairs == [(live.base, "INR"), (live.base, "USD")]


def test_a_providers_figures_are_the_hand_computed_sums(live: Fixture) -> None:
    # 6,000 costing 150 (120 spread and a 30 fee already folded in) and 4,000 costing 100, the
    # second on a date whose market rate was carried forward.
    a_remittance(
        live,
        sent="6000",
        received="133200",
        provider="Exchange house",
        implied="22.2000000000",
        market="22.5500000000",
        spread="120.0000",
        total="150.0000",
    )
    a_remittance(
        live,
        sent="4000",
        received="88800",
        provider="Exchange house",
        implied="22.2000000000",
        market="22.5500000000",
        spread="100.0000",
        total="100.0000",
        on=date(2026, 3, 2),
    )
    a_rate(live, date(2026, 3, 2), "INR", "0.0443", carried_from=date(2026, 3, 1))

    year = only_year(analysis())
    [group] = year["providers"]

    assert group["provider"] == "Exchange house"
    assert group["transferCount"] == 2
    assert group["sentSource"] == "10000.0000"
    assert group["costedSentSource"] == "10000.0000"
    assert group["spreadSource"] == "220.0000"
    assert group["totalCostSource"] == "250.0000"
    assert group["feesSource"] == "30.0000"
    assert group["effectiveCostPct"] == "2.5000"
    assert group["avgRate"] == "22.2000000000"
    assert group["approximateCount"] == 1
    assert year["totalEffectiveCostPct"] == "2.5000"


def test_an_uncosted_remittance_is_sent_but_not_costed(live: Fixture) -> None:
    a_remittance(
        live,
        sent="2000",
        received="2000",
        destination_currency=live.base,
        spread=None,
        total=None,
    )

    year = only_year(analysis())
    [group] = year["providers"]

    assert group["sentSource"] == "2000.0000"
    assert group["costedSentSource"] == "0.0000"
    assert group["feesSource"] is None
    assert group["totalCostSource"] is None
    assert group["effectiveCostPct"] is None
    assert year["totalCostSource"] is None
    assert year["totalEffectiveCostPct"] is None


def test_unattributed_money_is_reported_and_left_out_of_the_comparison(live: Fixture) -> None:
    a_remittance(
        live, sent="10000", received="222000", provider="Dear", spread="300.0000", total="300.0000"
    )
    a_remittance(
        live, sent="10000", received="222000", provider="Cheap", spread="100.0000", total="100.0000"
    )
    a_remittance(
        live, sent="50000", received="1110000", provider=None, spread="50.0000", total="50.0000"
    )

    year = only_year(analysis())

    assert [g["provider"] for g in year["providers"]] == ["Cheap", "Dear"]
    assert year["unattributed"]["provider"] is None
    assert year["unattributed"]["sentSource"] == "50000.0000"
    assert year["cheapestProvider"] == "Cheap"
    assert year["potentialSavingsSource"] == "200.0000"
    assert year["totalSentSource"] == "70000.0000"


def test_one_recorded_provider_yields_no_comparison(live: Fixture) -> None:
    a_remittance(
        live, sent="10000", received="222000", provider="Only", spread="100.0000", total="100.0000"
    )

    year = only_year(analysis())

    assert year["cheapestProvider"] is None
    assert year["potentialSavingsSource"] is None


def test_the_year_filter_selects_one_year_and_refuses_a_bad_one(live: Fixture) -> None:
    a_remittance(live, sent="1000", received="22200", on=MARCH, spread="10.0000", total="10.0000")
    a_remittance(
        live, sent="9000", received="199800", on=LAST_MARCH, spread="90.0000", total="90.0000"
    )

    both = only_year(analysis())
    assert both["year"] == 2026

    filtered = analysis(year=2025)
    assert only_year(filtered)["year"] == 2025
    assert only_year(filtered)["totalSentSource"] == "9000.0000"

    refused = client().get("/transfers/analysis", params={"year": 12345}, headers=ORIGIN)
    assert refused.status_code == 422


def test_another_households_remittances_never_appear(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO household (name, base_currency, secondary_currency)"
            " VALUES ('other', %s, %s) RETURNING id",
            (live.base, live.secondary),
        )
        other = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO person (household_id, display_name, is_self) VALUES (%s, 'other', true)",
            (other,),
        )
    live.conn.commit()

    a_remittance(
        live,
        sent="99000",
        received="2197800",
        provider="Not mine",
        spread="990.0000",
        total="990.0000",
        household=other,
    )

    assert analysis()["corridors"] == []


def test_a_non_remittance_transfer_is_outside_the_analysis(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="1000", received="22200", spread="10.0000", total="10.0000"
    )
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE transfer SET transfer_kind = 'investment_funding' WHERE id = %s",
            (transfer_id,),
        )
    live.conn.commit()

    assert analysis()["corridors"] == []


def test_a_rejected_remittance_is_outside_the_analysis(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="1000", received="22200", spread="10.0000", total="10.0000"
    )
    with live.conn.cursor() as cur:
        # A rejected pair keeps its row so the matcher does not re-propose it; its money was
        # never moved as one transfer, so no cost of it belongs in the analysis.
        cur.execute("UPDATE transfer SET status = 'rejected' WHERE id = %s", (transfer_id,))
    live.conn.commit()

    assert analysis()["corridors"] == []


def test_two_spellings_of_one_provider_stay_two_rows(live: Fixture) -> None:
    # Exact match by contract: merging them is the owner's correction to make, not the
    # analysis's guess, because two similar names can be two real channels.
    a_remittance(
        live,
        sent="1000",
        received="22200",
        provider="Exchange house",
        spread="10.0000",
        total="10.0000",
    )
    a_remittance(
        live,
        sent="1000",
        received="22200",
        provider="exchange house",
        spread="20.0000",
        total="20.0000",
    )

    year = only_year(analysis())

    assert sorted(g["provider"] for g in year["providers"]) == ["Exchange house", "exchange house"]


# ---------------------------------------------------------------------------
# PATCH /transfers/{id} — the owner's correction
# ---------------------------------------------------------------------------


def test_recording_a_provider_and_a_fee_recomputes_the_total_cost(live: Fixture) -> None:
    transfer_id = a_remittance(
        live,
        sent="5000",
        received="111000",
        provider=None,
        implied="22.2000000000",
        market="22.5500000000",
        spread="77.6000",
        total="77.6000",
    )

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={
            "provider": "  Exchange house  ",
            "explicitFeeAmount": "26.25",
            "explicitFeeCurrency": live.base,
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "Exchange house"
    assert body["explicitFeeAmount"] == "26.2500"
    # 77.60 spread + 26.25 fee, the design's own worked total.
    assert body["totalCostSource"] == "103.8500"
    # Confirmation-time figures are untouched.
    assert body["impliedRate"] == "22.2000000000"
    assert body["marketRate"] == "22.5500000000"
    assert body["fxSpreadCostSource"] == "77.6000"


def test_clearing_the_fee_returns_the_total_to_the_spread_alone(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )
    client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "26.25", "explicitFeeCurrency": live.base},
        headers=ORIGIN,
    )

    cleared = client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": None, "explicitFeeCurrency": None},
        headers=ORIGIN,
    )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["explicitFeeAmount"] is None
    assert cleared.json()["totalCostSource"] == "77.6000"


def test_an_identical_retry_recomputes_rather_than_accumulates(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )
    payload = {
        "provider": "Exchange house",
        "explicitFeeAmount": "26.25",
        "explicitFeeCurrency": live.base,
    }

    first = client().patch(f"/transfers/{transfer_id}", json=payload, headers=ORIGIN)
    second = client().patch(f"/transfers/{transfer_id}", json=payload, headers=ORIGIN)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["totalCostSource"] == first.json()["totalCostSource"] == "103.8500"


def test_a_fee_at_full_precision_round_trips_exactly(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="0.0000", total="0.0000"
    )

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "12.3456", "explicitFeeCurrency": live.base},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["explicitFeeAmount"] == "12.3456"
    assert response.json()["totalCostSource"] == "12.3456"


def test_a_fee_with_excess_precision_is_refused_before_storage(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "26.12345", "explicitFeeCurrency": live.base},
        headers=ORIGIN,
    )

    assert response.status_code == 422
    assert stored(live, transfer_id)[1] is None


def test_a_half_stated_fee_is_refused(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )

    response = client().patch(
        f"/transfers/{transfer_id}", json={"explicitFeeAmount": "26.25"}, headers=ORIGIN
    )

    assert response.status_code == 422


def test_an_unknown_fee_currency_is_refused(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "26.25", "explicitFeeCurrency": "ZZZ"},
        headers=ORIGIN,
    )

    assert response.status_code == 422


def test_a_blank_provider_is_refused_rather_than_silently_clearing(live: Fixture) -> None:
    transfer_id = a_remittance(
        live,
        sent="5000",
        received="111000",
        provider="Exchange house",
        spread="1.0000",
        total="1.0000",
    )

    response = client().patch(f"/transfers/{transfer_id}", json={"provider": "   "}, headers=ORIGIN)

    assert response.status_code == 422
    assert stored(live, transfer_id)[0] == "Exchange house"


def test_an_over_long_provider_is_refused(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="1.0000", total="1.0000"
    )

    response = client().patch(
        f"/transfers/{transfer_id}", json={"provider": "x" * 81}, headers=ORIGIN
    )

    assert response.status_code == 422


def test_a_refused_correction_leaves_every_field_as_it_was(live: Fixture) -> None:
    transfer_id = a_remittance(
        live,
        sent="5000",
        received="111000",
        provider="Exchange house",
        spread="77.6000",
        total="77.6000",
    )
    before = stored(live, transfer_id)

    # The provider is valid and the fee is not: nothing may be written.
    response = client().patch(
        f"/transfers/{transfer_id}",
        json={
            "provider": "Bank wire",
            "explicitFeeAmount": "26.25",
            "explicitFeeCurrency": "ZZZ",
        },
        headers=ORIGIN,
    )

    assert response.status_code == 422
    assert stored(live, transfer_id) == before


def test_a_fee_in_a_third_currency_is_translated_at_the_transfers_own_date(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )
    # 1 USD = 3.6725 base on the transfer's date, and a different rate the day after, so a
    # translation that reached for the wrong date would show it.
    a_rate(live, MARCH, "USD", "3.6725")
    a_rate(live, date(2026, 3, 2), "USD", "9.9999")

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "10", "explicitFeeCurrency": "USD"},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    # 10 USD × 3.6725 = 36.725 → 36.7250, plus the 77.60 spread.
    assert response.json()["totalCostSource"] == "114.3250"
    assert response.json()["explicitFeeCurrency"] == "USD"


def test_a_fee_currency_with_no_rate_for_that_date_is_refused(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "10", "explicitFeeCurrency": "USD"},
        headers=ORIGIN,
    )

    assert response.status_code == 422
    assert "no exchange rate" in response.json()["detail"]
    assert stored(live, transfer_id)[1] is None


def test_a_later_rate_change_never_moves_a_stored_cost_or_its_reconciliation(
    live: Fixture,
) -> None:
    transfer_id = a_remittance(
        live,
        sent="5000",
        received="111000",
        provider="Exchange house",
        spread="77.6000",
        total="77.6000",
    )
    a_rate(live, MARCH, "USD", "3.6725")
    client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "10", "explicitFeeCurrency": "USD"},
        headers=ORIGIN,
    )
    before = only_year(analysis())

    # The rate for that date is corrected afterwards. The frozen cost must not follow it.
    a_rate(live, MARCH, "USD", "1.0000")

    after = only_year(analysis())
    assert after == before
    group = after["providers"][0]
    assert group["totalCostSource"] == "114.3250"
    assert Decimal(group["feesSource"]) + Decimal(group["spreadSource"]) == Decimal(
        group["totalCostSource"]
    )


def test_a_provider_only_correction_never_retranslates_a_frozen_fee(live: Fixture) -> None:
    """A correction about no money must not re-derive one.

    Rebuilding the stored fee and translating it again on every `PATCH` let a later rate
    correction move a cost the owner had already frozen — through a request that named only the
    provider.
    """
    transfer_id = a_remittance(
        live, sent="5000", received="111000", provider=None, spread="77.6000", total="77.6000"
    )
    a_rate(live, MARCH, "USD", "3.6725")
    client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "10", "explicitFeeCurrency": "USD"},
        headers=ORIGIN,
    )
    frozen = stored(live, transfer_id)
    before = only_year(analysis())

    # That date's rate is corrected, then the owner labels the provider — a request about no
    # money at all.
    a_rate(live, MARCH, "USD", "1.0000")
    labelled = client().patch(
        f"/transfers/{transfer_id}", json={"provider": "Exchange house"}, headers=ORIGIN
    )

    assert labelled.status_code == 200, labelled.text
    assert labelled.json()["totalCostSource"] == "114.3250"
    # Only the provider moved.
    assert stored(live, transfer_id)[1:] == frozen[1:]
    assert only_year(analysis())["totalCostSource"] == before["totalCostSource"]


def test_a_provider_only_correction_survives_the_rates_removal(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", provider=None, spread="77.6000", total="77.6000"
    )
    a_rate(live, MARCH, "USD", "3.6725")
    client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "10", "explicitFeeCurrency": "USD"},
        headers=ORIGIN,
    )
    with live.conn.cursor() as cur:
        cur.execute("DELETE FROM fx_rate WHERE rate_date = %s AND base_currency = 'USD'", (MARCH,))
    live.conn.commit()

    labelled = client().patch(
        f"/transfers/{transfer_id}", json={"provider": "Exchange house"}, headers=ORIGIN
    )

    # Labelling a provider cannot depend on a rate the correction does not use.
    assert labelled.status_code == 200, labelled.text
    assert labelled.json()["totalCostSource"] == "114.3250"


def test_another_households_remittance_cannot_be_corrected(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO household (name, base_currency, secondary_currency)"
            " VALUES ('other', %s, %s) RETURNING id",
            (live.base, live.secondary),
        )
        other = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO person (household_id, display_name, is_self) VALUES (%s, 'other', true)",
            (other,),
        )
    live.conn.commit()
    transfer_id = a_remittance(
        live,
        sent="5000",
        received="111000",
        provider=None,
        spread="77.6000",
        total="77.6000",
        household=other,
    )
    before = stored(live, transfer_id)

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={
            "provider": "Mine now",
            "explicitFeeAmount": "26.25",
            "explicitFeeCurrency": live.base,
        },
        headers=ORIGIN,
    )

    assert response.status_code == 404
    assert stored(live, transfer_id) == before


def test_an_over_large_fee_is_refused_rather_than_overflowing(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", spread="77.6000", total="77.6000"
    )

    responses = [
        client().patch(
            f"/transfers/{transfer_id}",
            json={"explicitFeeAmount": amount, "explicitFeeCurrency": live.base},
            headers=ORIGIN,
        )
        for amount in ("1E+18", "999999999999999999999", "NaN", "+5")
    ]

    assert [r.status_code for r in responses] == [422, 422, 422, 422]
    assert stored(live, transfer_id)[1] is None


def test_a_rate_later_observed_moves_the_approximate_label_and_nothing_else(
    live: Fixture,
) -> None:
    a_remittance(
        live,
        sent="5000",
        received="111000",
        provider="Exchange house",
        implied="22.2000000000",
        market="22.5500000000",
        spread="77.6000",
        total="77.6000",
    )
    a_rate(live, MARCH, "INR", "0.0443", carried_from=date(2026, 2, 28))

    carried = only_year(analysis())["providers"][0]
    assert carried["approximateCount"] == 1

    # The real rate for that day arrives later. The cost stays frozen; only the label moves.
    a_rate(live, MARCH, "INR", "0.0443")

    observed = only_year(analysis())["providers"][0]
    assert observed["approximateCount"] == 0
    assert observed["totalCostSource"] == carried["totalCostSource"] == "77.6000"
    assert observed["effectiveCostPct"] == carried["effectiveCostPct"]


def test_a_third_currency_fee_on_a_carried_forward_rate_is_labelled_approximate(
    live: Fixture,
) -> None:
    transfer_id = a_remittance(
        live,
        sent="2000",
        received="2000",
        destination_currency=live.base,
        provider="Bank wire",
        spread=None,
        total=None,
    )
    # A same-currency remittance has no market rate, so only the fee's own rate can make its
    # cost approximate (Invariant 7).
    a_rate(live, MARCH, "USD", "3.6725", carried_from=date(2026, 2, 28))

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={"explicitFeeAmount": "10", "explicitFeeCurrency": "USD"},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["totalCostSource"] == "36.7250"
    assert response.json()["costIsApproximate"] is True
    assert only_year(analysis())["providers"][0]["approximateCount"] == 1


def test_an_unknown_foreign_rejected_or_non_remittance_transfer_is_all_one_answer(
    live: Fixture,
) -> None:
    rejected = a_remittance(live, sent="1000", received="22200", spread="1.0000", total="1.0000")
    other_kind = a_remittance(live, sent="1000", received="22200", spread="1.0000", total="1.0000")
    with live.conn.cursor() as cur:
        cur.execute("UPDATE transfer SET status = 'rejected' WHERE id = %s", (rejected,))
        cur.execute(
            "UPDATE transfer SET transfer_kind = 'internal_same_currency' WHERE id = %s",
            (other_kind,),
        )
    live.conn.commit()

    answers = [
        client()
        .patch(f"/transfers/{target}", json={"provider": "Anything"}, headers=ORIGIN)
        .status_code
        for target in (uuid4(), rejected, other_kind)
    ]

    assert answers == [404, 404, 404]


def test_a_session_that_is_not_the_owner_cannot_correct_a_remittance(live: Fixture) -> None:
    transfer_id = a_remittance(
        live, sent="5000", received="111000", provider=None, spread="77.6000", total="77.6000"
    )

    app.dependency_overrides[auth.require_owner] = _not_the_owner
    refused = client().patch(
        f"/transfers/{transfer_id}", json={"provider": "Exchange house"}, headers=ORIGIN
    )
    blocked_read = client().get("/transfers/analysis", headers=ORIGIN)
    app.dependency_overrides[auth.require_owner] = live.owner

    assert refused.status_code == 403
    assert blocked_read.status_code == 403
    assert stored(live, transfer_id)[0] is None


def test_a_corrected_remittances_legs_stay_out_of_spending(live: Fixture) -> None:
    transfer_id = a_remittance(
        live,
        sent="5000",
        received="111000",
        provider=None,
        spread="77.6000",
        total="77.6000",
        confirm_legs=True,
    )

    response = client().patch(
        f"/transfers/{transfer_id}",
        json={
            "provider": "Exchange house",
            "explicitFeeAmount": "26.25",
            "explicitFeeCurrency": live.base,
        },
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text

    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM v_transaction_reportable WHERE transfer_id = %s", (transfer_id,)
        )
        reportable = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT count(*) FROM transaction WHERE transfer_id = %s", (transfer_id,))
        legs = cur.fetchone()[0]  # type: ignore[index]

    assert legs == 2
    assert reportable == 0
