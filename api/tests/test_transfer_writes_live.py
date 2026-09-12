"""The transfer write routes against a real database: confirm, link by hand, unlink.

The acceptance for slice 1.3's transfer half is asserted here through the route, not inferred
from the view: a remittance and a card payment are each confirmed and each takes exactly its
source amount out of spending, and unlinking puts exactly that back. The FX cost is checked
against a hand computation to the last stored digit (`testing-and-data-quality.html` §4).

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, Fixture, _clean, _connect
from .test_transfers_live import MARCH, a_leg, an_account, candidates, client, pairs


class Owner(Fixture):
    """`confirm_transfer()` and `unlink_transfer()` call `assert_is_owner()`, which looks the
    session's user up in `app_user` — so, unlike the read suite, this one needs an owner row
    that exists."""

    def __init__(self, conn: psycopg.Connection) -> None:
        super().__init__(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM person WHERE household_id = %s AND is_self", (self.household_id,)
            )
            person = cur.fetchone()[0]  # type: ignore[index]
            self.user_id = uuid4()
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (
                    self.user_id,
                    self.household_id,
                    person,
                    f"transfers-{self.user_id}@example.invalid",
                ),
            )
        conn.commit()

    def owner(self) -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=self.user_id,
            household_id=self.household_id,
            session_id=UUID(int=3),
            email="o@e.com",
        )


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Owner]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
    fixture: Owner | None = None
    try:
        fixture = Owner(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            if fixture is not None:
                cur.execute("DELETE FROM app_user WHERE id = %s", (fixture.user_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def spending(live: Owner) -> Decimal:
    """Spending as a report reads it: the reportable view, in the base currency (I3)."""
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(-sum(amount_base), 0) FROM v_transaction_reportable
                WHERE household_id = %s AND amount_base < 0""",
            (live.household_id,),
        )
        return Decimal(cur.fetchone()[0])  # type: ignore[index]


def a_rate(live: Owner, on: date, rate: str, carried_from: date | None = None) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source,
                                    is_carried_forward, carried_from)
               VALUES (%s, %s, %s, %s, 'manual', %s, %s)
               ON CONFLICT (rate_date, base_currency, quote_currency)
               DO UPDATE SET rate = EXCLUDED.rate,
                             is_carried_forward = EXCLUDED.is_carried_forward,
                             carried_from = EXCLUDED.carried_from""",
            (on, live.secondary, live.base, Decimal(rate), carried_from is not None, carried_from),
        )
    live.conn.commit()


def confirm(debit: UUID, credit: UUID, *, proposed: bool = True) -> httpx.Response:
    response: httpx.Response = client().post(
        "/transfers",
        json={
            "sourceTransactionId": str(debit),
            "destinationTransactionId": str(credit),
            "fromCandidate": proposed,
        },
        headers=ORIGIN,
    )
    return response


def transfer_count(live: Owner) -> int:
    with live.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM transfer")
        return int(cur.fetchone()[0])  # type: ignore[index]


# ---------------------------------------------------------------------------------------
# The acceptance: exactly the source amount leaves spending, and exactly that comes back.


def test_confirming_a_remittance_takes_exactly_its_source_amount_out_of_spending(
    live: Owner,
) -> None:
    """100 of the secondary currency leaves at 4 per unit — 400 in the base — and 392 arrives.
    Spending, in the base, must drop by exactly the source leg's 400: not the 392 that arrived
    and not the difference."""
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
    credit = a_leg(live, live.account_id, "392.0000", date(2026, 3, 3), "INWARD REMIT")
    before = spending(live)

    confirmed = confirm(debit, credit)

    assert confirmed.status_code == 201, confirmed.text
    body = confirmed.json()
    assert body["status"] == "confirmed"
    assert body["transferKind"] == "remittance"
    assert before - spending(live) == Decimal("400.0000")


def test_confirming_a_card_payment_takes_exactly_the_payment_out_of_spending(
    live: Owner,
) -> None:
    """Invariant 4 through the route: the 310 purchase is the spending and the 3,900 payment
    that settles the card is not."""
    card = an_account(live, account_type="credit_card")
    a_leg(live, card, "-310.0000", MARCH, "GROCER")
    payment = a_leg(live, live.account_id, "-3900.0000", MARCH + timedelta(days=4), "CARD PAYMENT")
    settled = a_leg(live, card, "3900.0000", MARCH + timedelta(days=5), "PAYMENT RECEIVED")
    before = spending(live)

    confirmed = confirm(payment, settled)

    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["transferKind"] == "card_payment"
    assert before - spending(live) == Decimal("3900.0000")
    assert spending(live) == Decimal("310.0000")


def test_unlinking_restores_spending_exactly_and_does_not_propose_the_pair_again(
    live: Owner,
) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")
    before = spending(live)
    transfer = confirm(debit, credit).json()["id"]

    unlinked = client().delete(f"/transfers/{transfer}", headers=ORIGIN)

    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["status"] == "rejected"
    assert spending(live) == before
    assert (str(debit), str(credit)) not in pairs(candidates())


# ---------------------------------------------------------------------------------------
# What the movement cost. §5 of multi-currency-and-fx.html owns the formulas; this is its own
# worked example, and each expected figure below is the hand computation.


def test_a_cross_currency_confirmation_stores_its_cost_to_the_last_digit(live: Owner) -> None:
    """5,000 leaves, 111,000 arrives, and the market held 22.55 that day.

    implied  = 111,000 ÷ 5,000                    = 22.2000000000
    spread   = (22.55 − 22.20) ÷ 22.55 × 100      =  1.5521   (4 places, half up)
    cost     = 5,000 × 1.5521 ÷ 100               = 77.6050
    total    = cost + no stated fee               = 77.6050

    The cost is derived from the *stored* percentage rather than the unrounded one, so every
    stored figure can be recomputed from the figures stored beside it — the rule
    `money._frozen` applies to a transaction's frozen amounts.
    """
    on = date(2026, 3, 10)
    a_rate(live, on, "22.5500000000")
    abroad = an_account(live, account_type="current", currency=live.secondary)
    debit = a_leg(
        live, abroad, "-5000.0000", on, "OUTWARD REMIT", currency=live.secondary, to_base="22.55"
    )
    credit = a_leg(live, live.account_id, "111000.0000", on + timedelta(days=1), "INWARD REMIT")

    body = confirm(debit, credit).json()

    assert body["impliedRate"] == "22.2000000000"
    assert body["marketRate"] == "22.5500000000"
    assert body["fxSpreadPct"] == "1.5521"
    assert body["fxSpreadCostSource"] == "77.6050"
    assert body["totalCostSource"] == "77.6050"
    assert body["explicitFeeAmount"] is None
    assert body["costIsApproximate"] is False
    assert body["sourceAmount"] == "5000.0000"
    assert body["destinationAmount"] == "111000.0000"


def test_a_cost_derived_from_a_carried_rate_says_so(live: Owner) -> None:
    """Invariant 7. A Saturday's rate is Friday's, carried; a cost computed from it is not a
    cost anyone observed, and the flag must reach the wire on confirmation and on every read
    after."""
    on = date(2026, 3, 14)
    a_rate(live, on, "22.5500000000", carried_from=date(2026, 3, 13))
    abroad = an_account(live, account_type="current", currency=live.secondary)
    debit = a_leg(
        live, abroad, "-5000.0000", on, "OUTWARD REMIT", currency=live.secondary, to_base="22.55"
    )
    credit = a_leg(live, live.account_id, "111000.0000", on + timedelta(days=1), "INWARD REMIT")

    confirmed = confirm(debit, credit).json()
    listed = client().get("/transfers", headers=ORIGIN).json()["transfers"]

    assert confirmed["costIsApproximate"] is True
    assert [t["costIsApproximate"] for t in listed] == [True]


def test_a_cross_currency_pair_with_no_rate_records_only_what_was_observed(live: Owner) -> None:
    """The implied rate is a fact the two legs state. The market rate is not held for the day,
    so neither it nor anything derived from it is written — a blank, not a guess."""
    on = date(2026, 3, 20)
    abroad = an_account(live, account_type="current", currency=live.secondary)
    debit = a_leg(
        live, abroad, "-5000.0000", on, "OUTWARD REMIT", currency=live.secondary, to_base="22.55"
    )
    credit = a_leg(live, live.account_id, "111000.0000", on + timedelta(days=1), "INWARD REMIT")

    body = confirm(debit, credit).json()

    assert body["impliedRate"] == "22.2000000000"
    assert body["marketRate"] is None
    assert body["fxSpreadPct"] is None
    assert body["fxSpreadCostSource"] is None
    assert body["totalCostSource"] is None
    assert body["costIsApproximate"] is False


def test_no_cost_is_never_an_approximate_cost(live: Owner) -> None:
    """The flag qualifies a figure, so with no figure there is nothing to qualify. Between two
    currencies neither of which is the base, one leg's rate can be held — carried from the day
    before — while the other's is not: there is then no market rate and no cost, and a
    carried row sitting in `fx_rate` must not make the blank read as an estimate."""
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT min(code) FROM currency WHERE code NOT IN (%s, %s)", (live.base, live.secondary)
        )
        third = cur.fetchone()[0]  # type: ignore[index]
    on = date(2026, 3, 21)
    a_rate(live, on, "22.5500000000", carried_from=date(2026, 3, 20))
    abroad = an_account(live, account_type="current", currency=live.secondary)
    elsewhere = an_account(live, account_type="current", currency=third)
    debit = a_leg(
        live, abroad, "-5000.0000", on, "OUTWARD REMIT", currency=live.secondary, to_base="22.55"
    )
    credit = a_leg(
        live,
        elsewhere,
        "4000.0000",
        on + timedelta(days=1),
        "INWARD REMIT",
        currency=third,
        to_base="2",
    )

    body = confirm(debit, credit).json()

    assert body["marketRate"] is None
    assert body["totalCostSource"] is None
    assert body["costIsApproximate"] is False


def test_a_same_currency_transfer_records_no_fx_cost(live: Owner) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "495.0000", MARCH, "INWARD TRANSFER")

    body = confirm(debit, credit).json()

    assert body["impliedRate"] is None
    assert body["marketRate"] is None
    assert body["totalCostSource"] is None


# ---------------------------------------------------------------------------------------
# Provenance of the link.


def test_a_proposed_pair_is_recorded_as_detected_with_its_confidence(live: Owner) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")

    body = confirm(debit, credit, proposed=True).json()

    assert body["matchMethod"] == "auto"
    assert isinstance(body["matchConfidence"], str)
    assert Decimal("0.5") <= Decimal(body["matchConfidence"]) <= Decimal("1")


def test_a_pair_the_owner_picked_is_recorded_as_theirs(live: Owner) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "720.0000", MARCH + timedelta(days=12), "QQQQ")

    body = confirm(debit, credit, proposed=False).json()

    assert body["matchMethod"] == "user_created"
    assert body["matchConfidence"] is None


# ---------------------------------------------------------------------------------------
# Refusals. Each writes nothing.


def test_a_leg_already_confirmed_cannot_be_confirmed_again(live: Owner) -> None:
    other = an_account(live)
    third = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(live, other, "500.0000", MARCH, "INWARD TRANSFER")
    elsewhere = a_leg(live, third, "500.0000", MARCH, "INWARD TRANSFER")
    assert confirm(debit, credit).status_code == 201
    count = transfer_count(live)

    again = confirm(debit, elsewhere)

    assert again.status_code == 409
    assert transfer_count(live) == count


def test_another_household_s_transaction_cannot_be_named(live: Owner) -> None:
    """404, not 422: telling the two apart would tell a caller whether a guessed id exists."""
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

    refused = confirm(mine, stranger)

    assert refused.status_code == 404
    assert transfer_count(live) == 0


@pytest.mark.parametrize("shape", ["reversed", "one_account"])
def test_a_pair_that_cannot_be_a_transfer_is_refused(live: Owner, shape: str) -> None:
    other = an_account(live)
    debit = a_leg(live, live.account_id, "-500.0000", MARCH, "OUTWARD TRANSFER")
    credit = a_leg(
        live, live.account_id if shape == "one_account" else other, "500.0000", MARCH, "INWARD"
    )

    refused = confirm(credit, debit) if shape == "reversed" else confirm(debit, credit)

    assert refused.status_code == 422
    assert transfer_count(live) == 0


def test_unlinking_a_transfer_that_is_not_there_is_not_found(live: Owner) -> None:
    missing = client().delete(f"/transfers/{uuid4()}", headers=ORIGIN)

    assert missing.status_code == 404
