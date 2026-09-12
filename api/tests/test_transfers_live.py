"""The transfer read routes against a real database.

`GET /transfers/candidates` is the one that matters most, and the first test here is the one
ADR-0017 is judged on: listing candidates writes nothing. Scoring proposes; only a request
naming the pair links it, and that request is 3b3b's.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, Fixture, _clean, _connect

MARCH = date(2026, 3, 1)


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
            # The ledger first — TRUNCATE ... transaction CASCADE takes `transfer` with it —
            # then every account and household this suite invented. `db/tests/0006` asserts
            # the seed creates at most one household, and a leftover fails the next push.
            _clean(cur)
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def client() -> TestClient:
    return TestClient(app)


def an_account(
    live: Fixture,
    account_type: str = "savings",
    currency: str | None = None,
    household: UUID | None = None,
) -> UUID:
    """Named like the fixture's own account so `_clean` removes it with that one."""
    owner = household or live.household_id
    with live.conn.cursor() as cur:
        cur.execute("SELECT id FROM person WHERE household_id = %s ORDER BY is_self DESC", (owner,))
        person = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                    country, opening_balance_date, current_balance, credit_limit)
               VALUES (%s, %s, 'route test', %s, %s, 'ZZ', DATE '2026-01-01', 0, %s)
               RETURNING id""",
            (
                owner,
                person,
                account_type,
                currency or live.base,
                Decimal("50000") if account_type == "credit_card" else None,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def a_leg(
    live: Fixture,
    account: UUID,
    amount: str,
    on: date,
    description: str,
    *,
    currency: str | None = None,
    to_base: str = "1",
    reference: str | None = None,
    household: UUID | None = None,
) -> UUID:
    """One committed row. A leg in another currency carries that currency's rate to base; the
    secondary figure is left at parity, which V10's derivation check accepts and nothing here
    reads."""
    value = Decimal(amount)
    rate = Decimal(to_base)
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        description_clean, reference_raw, amount, currency,
                                        fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                        amount_base, amount_secondary, txn_type, source,
                                        dedupe_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, 'manual', %s)
               RETURNING id""",
            (
                household or live.household_id,
                account,
                on,
                description,
                description,
                reference,
                value,
                currency or live.base,
                on,
                rate,
                (value * rate).quantize(Decimal("0.0001")),
                value,
                "income" if value > 0 else "expense",
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def ledger_state(live: Fixture) -> tuple[int, list[tuple[Any, ...]]]:
    with live.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM transfer")
        transfers = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT id, transfer_id, txn_type, updated_at FROM transaction ORDER BY id")
        return transfers, list(cur.fetchall())


def candidates(**query: object) -> list[dict[str, Any]]:
    params = {"dateFrom": str(MARCH), "dateTo": str(MARCH + timedelta(days=30)), **query}
    response = client().get("/transfers/candidates", params=params, headers=ORIGIN)
    assert response.status_code == 200, response.text
    found: list[dict[str, Any]] = response.json()["candidates"]
    return found


def pairs(found: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(c["debit"]["id"], c["credit"]["id"]) for c in found}


def link(
    live: Fixture, debit: UUID, credit: UUID, source: UUID, destination: UUID, status: str
) -> UUID:
    """A transfer row written directly: these tests are about what the read routes do with one,
    not about how one is made."""
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                                     source_transaction_id, destination_transaction_id,
                                     source_amount, source_currency, destination_amount,
                                     destination_currency, transfer_date, transfer_kind,
                                     match_method, status)
               SELECT %s, %s, %s, d.id, c.id, abs(d.amount), d.currency, c.amount, c.currency,
                      d.booked_date, 'internal_same_currency', 'user_confirmed',
                      %s::transfer_status
                 FROM transaction d, transaction c WHERE d.id = %s AND c.id = %s
               RETURNING id""",
            (live.household_id, source, destination, status, debit, credit),
        )
        transfer = cur.fetchone()[0]  # type: ignore[index]
        if status == "confirmed":
            cur.execute(
                "UPDATE transaction SET transfer_id = %s, txn_type = 'transfer_out' WHERE id = %s",
                (transfer, debit),
            )
            cur.execute(
                "UPDATE transaction SET transfer_id = %s, txn_type = 'transfer_in' WHERE id = %s",
                (transfer, credit),
            )
    live.conn.commit()
    return UUID(str(transfer))


# ---------------------------------------------------------------------------------------
# Candidates across the ledger.


def test_listing_candidates_writes_nothing(live: Fixture) -> None:
    """ADR-0017: detection proposes and nothing links without a click. The strongest pair in
    this ledger is still only a suggestion, so asking for it moves no row and makes no
    transfer."""
    other = an_account(live)
    a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")
    before = ledger_state(live)

    found = candidates()

    assert found, "the pair must be proposed, or this proves nothing"
    assert ledger_state(live) == before


def test_candidate_payloads_redact_identifiers_without_changing_scoring(live: Fixture) -> None:
    other = an_account(live)
    private_identifier = "1234-٥٦٧٨-9012-3456"
    debit = a_leg(
        live,
        live.account_id,
        "-500.0000",
        MARCH,
        f"OUTWARD TRANSFER {private_identifier}",
    )
    a_leg(
        live,
        other,
        "500.0000",
        MARCH,
        f"INWARD TRANSFER {private_identifier}",
    )

    response = client().get(
        "/transfers/candidates",
        params={"dateFrom": str(MARCH), "dateTo": str(MARCH + timedelta(days=30))},
        headers=ORIGIN,
    )

    assert response.status_code == 200
    [candidate] = [
        item for item in response.json()["candidates"] if item["debit"]["id"] == str(debit)
    ]
    assert candidate["score"] > 0
    assert private_identifier not in response.text
    assert "1234567890123456" not in response.text
    assert candidate["debit"]["descriptionRaw"] == "OUTWARD TRANSFER [redacted]"


def test_an_internal_move_is_proposed_and_names_its_kind(live: Fixture) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")

    [proposed] = [c for c in candidates() if c["debit"]["id"] == str(debit)]

    assert proposed["credit"]["id"] == str(credit)
    assert proposed["proposedKind"] == "internal_same_currency"
    assert proposed["band"] in {"high", "probable", "weak"}
    assert 0.5 <= proposed["score"] <= 1.0


def test_money_arriving_on_a_card_is_proposed_as_a_card_payment(live: Fixture) -> None:
    card = an_account(live, account_type="credit_card")
    debit = a_leg(live, live.account_id, "-3900.0000", MARCH, "CARD PAYMENT TRANSFER")
    a_leg(live, card, "3900.0000", MARCH + timedelta(days=2), "PAYMENT RECEIVED TRANSFER")

    [proposed] = [c for c in candidates() if c["debit"]["id"] == str(debit)]

    assert proposed["proposedKind"] == "card_payment"


def test_a_leg_already_linked_is_never_proposed(live: Fixture) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")
    link(live, debit, credit, live.account_id, other, "confirmed")

    found = candidates()

    assert str(debit) not in {c["debit"]["id"] for c in found}
    assert str(credit) not in {c["credit"]["id"] for c in found}


def test_a_linked_credit_is_not_offered_to_a_different_debit(live: Fixture) -> None:
    """The exclusion is per leg, on both sides. A credit already claimed by one transfer is not
    a candidate destination for some other debit that happens to match it — linking it would
    be refused anyway (V20), and proposing it would ask the owner a question with no yes."""
    other = an_account(live)
    third = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")
    link(live, debit, credit, live.account_id, other, "confirmed")
    stray = a_leg(live, third, "-500.0000", MARCH, "OUTWARD TRANSFER")

    found = candidates()

    assert (str(stray), str(credit)) not in pairs(found)


def test_a_rejected_pair_is_not_proposed_again_but_its_legs_still_are(live: Fixture) -> None:
    """Unlinking leaves the transfer `rejected` precisely so the owner is not asked the same
    question twice (V20). The exclusion is of the pair, not of the legs: the debit may still
    be the source of a different transfer."""
    other = an_account(live)
    third = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    refused = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")
    alternative = a_leg(live, third, "500.0000", MARCH, "INWARD TRANSFER")
    link(live, debit, refused, live.account_id, other, "rejected")

    found = pairs(candidates())

    assert (str(debit), str(refused)) not in found
    assert (str(debit), str(alternative)) in found


def test_a_cross_currency_pair_is_scored_at_the_rate_for_its_date(live: Fixture) -> None:
    """The fixture holds 1 secondary = 4 base on 1 and 2 March. 100 out and 392 in is a 2%
    spread — an exchange house's ordinary take — and scores; the same pair with no rate held
    for the day scores lower, because nothing can say whether the amounts agree (I7)."""
    abroad = an_account(live, account_type="current", currency=live.secondary)
    debit = a_leg(
        live,
        abroad,
        "-100.0000",
        date(2026, 3, 2),
        "OUTWARD REMIT",
        currency=live.secondary,
        to_base="4",
    )
    a_leg(live, live.account_id, "392.0000", date(2026, 3, 3), "INWARD REMIT")

    [rated] = [c for c in candidates() if c["debit"]["id"] == str(debit)]
    with live.conn.cursor() as cur:
        cur.execute("DELETE FROM fx_rate WHERE rate_date = DATE '2026-03-02'")
    live.conn.commit()
    unrated = [c for c in candidates() if c["debit"]["id"] == str(debit)]

    assert rated["proposedKind"] == "remittance"
    assert not unrated or unrated[0]["score"] < rated["score"]


def test_a_pair_between_accounts_linked_before_ranks_higher(live: Fixture) -> None:
    """§3's pair history: a pair of accounts the owner has confirmed transfers between is more
    likely to be one again. Two otherwise identical pairs, one between accounts with a history,
    must not score the same."""
    usual = an_account(live)
    fresh = an_account(live)
    link(
        live,
        a_leg(live, live.account_id, "-80.0000", MARCH, "OUTWARD TRANSFER"),
        a_leg(live, usual, "80.0000", MARCH, "INWARD TRANSFER"),
        live.account_id,
        usual,
        "confirmed",
    )
    debit = a_leg(live, live.account_id, "-500.0000", MARCH + timedelta(days=5), "OUTWARD TRANSFER")
    to_usual = a_leg(live, usual, "500.0000", MARCH + timedelta(days=5), "INWARD TRANSFER")
    to_fresh = a_leg(live, fresh, "500.0000", MARCH + timedelta(days=5), "INWARD TRANSFER")

    scores = {c["credit"]["id"]: c["score"] for c in candidates() if c["debit"]["id"] == str(debit)}

    assert scores[str(to_usual)] > scores[str(to_fresh)]


def test_the_window_is_bounded(live: Fixture) -> None:
    """The scan is a ledger joined against itself; an unbounded one is a request that holds a
    connection for as long as the ledger is long."""
    too_wide = client().get(
        "/transfers/candidates",
        params={"dateFrom": "2024-01-01", "dateTo": "2026-01-02"},
        headers=ORIGIN,
    )
    backwards = client().get(
        "/transfers/candidates",
        params={"dateFrom": "2026-03-10", "dateTo": "2026-03-01"},
        headers=ORIGIN,
    )

    assert too_wide.status_code == 422
    assert backwards.status_code == 422


def test_a_debit_outside_the_window_is_not_scanned(live: Fixture) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")

    found = candidates(dateFrom="2026-04-01", dateTo="2026-04-30")

    assert str(debit) not in {c["debit"]["id"] for c in found}


def test_money_in_a_candidate_is_a_string_never_a_json_number(live: Fixture) -> None:
    other = an_account(live)
    a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")

    [proposed] = candidates()

    assert proposed["debit"]["amount"] == "-500.0000"
    assert proposed["credit"]["amount"] == "500.0000"
    assert isinstance(proposed["debit"]["amountBase"], str)


def test_the_default_window_ends_at_the_ledger_s_latest_row(live: Fixture) -> None:
    """A ledger that stops in March is not empty because today is September. Anchored to
    today, an owner whose statements lag would be told there is nothing to confirm."""
    other = an_account(live)
    a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")

    response = client().get("/transfers/candidates", headers=ORIGIN)

    assert response.status_code == 200, response.text
    assert len(response.json()["candidates"]) == 1


# ---------------------------------------------------------------------------------------
# Peers for one transaction, for manual linking.


def test_peers_are_ranked_and_include_what_the_candidate_list_hides(live: Fixture) -> None:
    """§3: below 0.50 is not shown, but is findable by manual linking — which is this route.
    Hiding a weak peer here too would leave the owner no way to link a pair the scorer
    under-rates."""
    other = an_account(live)
    third = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    exact = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")
    weak = a_leg(live, third, "740.0000", MARCH + timedelta(days=2), "QQQQ")

    response = client().get(f"/transactions/{debit}/transfer-candidates", headers=ORIGIN)

    assert response.status_code == 200, response.text
    peers = response.json()["peers"]
    assert [p["credit"]["id"] for p in peers][:1] == [str(exact)]
    assert str(weak) in {p["credit"]["id"] for p in peers}
    assert str(weak) not in {c["credit"]["id"] for c in candidates()}


def test_a_credit_s_peers_are_the_debits_it_could_have_come_from(live: Fixture) -> None:
    """Money leaves before it arrives, so a credit looks *back* for its debit. Dated two days
    earlier on purpose: a same-day pair would pass whichever way the window pointed."""
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "500.0000", MARCH + timedelta(days=2), "INWARD TRANSFER")

    peers = client().get(f"/transactions/{credit}/transfer-candidates", headers=ORIGIN).json()

    assert [p["debit"]["id"] for p in peers["peers"]] == [str(debit)]


def test_another_household_s_transaction_is_not_found(live: Fixture) -> None:
    """404, not 422 and not an empty list: telling the two apart would tell a caller whether
    an id they guessed exists."""
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('theirs', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        theirs = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO person (household_id, display_name, is_self) VALUES (%s, 'them', true)",
            (theirs,),
        )
    live.conn.commit()
    their_account = an_account(live, household=theirs)
    stranger = a_leg(live, their_account, "500.0000", MARCH, "INWARD TRANSFER", household=theirs)
    mine = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")

    looked_up = client().get(f"/transactions/{stranger}/transfer-candidates", headers=ORIGIN)
    my_peers = client().get(f"/transactions/{mine}/transfer-candidates", headers=ORIGIN).json()

    assert looked_up.status_code == 404
    assert str(stranger) not in {p["credit"]["id"] for p in my_peers["peers"]}
    assert str(stranger) not in {c["credit"]["id"] for c in candidates()}


# ---------------------------------------------------------------------------------------
# Transfers already decided.


def test_decided_transfers_are_listed_with_money_as_strings(live: Fixture) -> None:
    other = an_account(live)
    confirmed = link(
        live,
        a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER"),
        a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER"),
        live.account_id,
        other,
        "confirmed",
    )
    rejected = link(
        live,
        a_leg(live, live.account_id, "-70.0000", MARCH, "OUTWARD TRANSFER"),
        a_leg(live, other, "70.0000", MARCH, "INWARD TRANSFER"),
        live.account_id,
        other,
        "rejected",
    )

    everything = client().get("/transfers", headers=ORIGIN).json()
    only_confirmed = (
        client().get("/transfers", params={"status": "confirmed"}, headers=ORIGIN).json()
    )

    assert {t["id"] for t in everything["transfers"]} == {str(confirmed), str(rejected)}
    assert everything["total"] == 2
    assert [t["id"] for t in only_confirmed["transfers"]] == [str(confirmed)]
    row = only_confirmed["transfers"][0]
    assert row["sourceAmount"] == "500.0000"
    assert isinstance(row["destinationAmount"], str)


def test_a_status_the_table_does_not_hold_is_refused(live: Fixture) -> None:
    """Candidates are computed, not stored (ADR-0017), so `candidate` is not a status this
    list can be asked for."""
    response = client().get("/transfers", params={"status": "candidate"}, headers=ORIGIN)

    assert response.status_code == 422
