"""Phase 4.1 Task 6: payment channels and every figure the Subscriptions screen reads — equivalents,
summary, upcoming, detail and by-account (subscription registry plan, "Phase 4.1 Task 6").

Reuses `test_recurring_live.py`'s `live` fixture and helpers, and
`test_subscription_periods_live.py`'s `from_transaction`/`_first`/`_by_id`. Several tests build a
series and its periods/members directly
with SQL (`_raw_series`/`_raw_period`/`_raw_member`) rather than through refresh, so a window
boundary (today, today+30, today-90, the 12-month cutoff) lands on an exact day instead of whatever
day a detection cadence happens to produce. Every name, label and amount is invented.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID, uuid4

from oneledger import auth
from oneledger.main import app
from oneledger.routes.recurring import _months_back

from .test_ingest_route_live import ORIGIN
from .test_recurring_live import (  # noqa: F401
    TODAY,
    _foreign_spend,
    a_monthly_group,
    live,
    patch_series,
    refresh,
    the_recurring,
)
from .test_spending_live import _another_household, a_spend
from .test_subscription_periods_live import _by_id, _first, from_transaction
from .test_transfer_writes_live import Owner
from .test_transfers_live import client

_FOUR_DP = Decimal("0.0001")

CARD_NUMBER = "SYNTHETIC-CARD-4242"


def _charges_per_year(cadence: str, custom_days: int | None) -> Decimal:
    table = {"weekly": 52, "monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}
    if cadence == "custom":
        assert custom_days is not None
        return Decimal(365) / Decimal(custom_days)
    return Decimal(table[cadence])


def _equivalents(amount: str, cadence: str, custom_days: int | None) -> tuple[str, str]:
    """A hand computation of the annual/monthly equivalents, independent of
    `oneledger.recurring.equivalents` — the same "re-derive independently" shape
    `test_transfer_writes_live.py` uses for its own FX-cost check."""
    cpy = _charges_per_year(cadence, custom_days)
    annual_unrounded = Decimal(amount) * cpy
    annual = annual_unrounded.quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN)
    monthly = (annual_unrounded / 12).quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN)
    return str(annual), str(monthly)


def _old_account(live: Owner) -> UUID:  # noqa: F811
    """`test_ingest_route_live.Fixture`'s own account opens 2026-01-01; the 12-months window test
    needs charges further back than that, so this one gets its own account, opened well before."""
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM person WHERE household_id = %s ORDER BY is_self DESC",
            (live.household_id,),
        )
        person = cur.fetchone()
        assert person is not None
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                    country, opening_balance_date, current_balance)
               VALUES (%s, %s, 'route test old', 'savings', %s, 'ZZ', %s, 0)
               RETURNING id""",
            (live.household_id, person[0], live.base, TODAY - timedelta(days=1000)),
        )
        found = cur.fetchone()
        assert found is not None
        account_id = found[0]
    live.conn.commit()
    return UUID(str(account_id))


def _raw_series(
    live: Owner,  # noqa: F811
    *,
    label: str,
    first_seen_on: date,
    last_seen_on: date,
    cadence: str = "monthly",
    custom_interval_days: int | None = None,
    is_subscription: bool = True,
    native_amount: str = "10.0000",
) -> UUID:
    """A series built directly, bypassing refresh, so a window-boundary test can put its dates
    exactly where the boundary is rather than wherever a detected cadence would land them."""
    with live.conn.cursor() as cur:
        cur.execute("SELECT base_currency FROM household WHERE id = %s", (live.household_id,))
        base = str(cur.fetchone()[0]).strip()  # type: ignore[index]
        cur.execute(
            """INSERT INTO recurring_series (household_id, idempotency_key, label, match_key,
                   cadence, custom_interval_days, expected_amount_base, currency, first_seen_on,
                   last_seen_on, is_subscription, subscription_set_by_owner, detection_method,
                   named_by_owner, native_amount, native_currency)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, 'owner', true, %s, %s)
               RETURNING id""",
            (
                live.household_id,
                uuid4(),
                label,
                label.lower(),
                cadence,
                custom_interval_days,
                native_amount,
                base,
                first_seen_on,
                last_seen_on,
                is_subscription,
                native_amount,
                base,
            ),
        )
        found = cur.fetchone()
        assert found is not None
        series_id = found[0]
    live.conn.commit()
    return UUID(str(series_id))


def _raw_period(
    live: Owner,  # noqa: F811
    series_id: UUID,
    started_on: date,
    *,
    ended_on: date | None = None,
    end_reason: str | None = None,
) -> UUID:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on,
                   end_reason)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (live.household_id, series_id, started_on, ended_on, end_reason),
        )
        found = cur.fetchone()
        assert found is not None
        period_id = found[0]
    live.conn.commit()
    return UUID(str(period_id))


def _raw_member(
    live: Owner,  # noqa: F811
    series_id: UUID,
    transaction_id: UUID,
    observed_on: date,
    amount_base: str,
) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO recurring_series_member (household_id, series_id, transaction_id,
                   observed_on, amount_base)
               VALUES (%s, %s, %s, %s, %s)""",
            (live.household_id, series_id, transaction_id, observed_on, amount_base),
        )
    live.conn.commit()


# ---------------------------------------------------------------------------------------
# Equivalents


_CADENCES: list[tuple[str, int | None]] = [
    ("weekly", None),
    ("monthly", None),
    ("quarterly", None),
    ("semiannual", None),
    ("annual", None),
    ("custom", 45),
]


def test_equivalents_exact_for_every_cadence_in_original_and_base_currency(live: Owner) -> None:  # noqa: F811
    for cadence, custom_days in _CADENCES:
        label = f"Synthetic equivalents {cadence}"
        txn_id = _foreign_spend(live, "-9.9900", "EUR", "-18.0000", TODAY, label)
        created = from_transaction(
            transactionId=str(txn_id),
            cadence=cadence,
            customIntervalDays=custom_days,
            label=label,
            isSubscription=True,
            idempotencyKey=str(uuid4()),
        )
        expected_annual, expected_monthly = _equivalents("9.99", cadence, custom_days)
        expected_annual_base, expected_monthly_base = _equivalents("18", cadence, custom_days)
        assert created["nativeAmount"] == "9.9900"
        assert created["nativeCurrency"] == "EUR"
        assert created["annualAmount"] == expected_annual, cadence
        assert created["monthlyAmount"] == expected_monthly, cadence
        assert created["annualAmountBase"] == expected_annual_base, cadence
        assert created["monthlyAmountBase"] == expected_monthly_base, cadence


def test_custom_cadence_equivalents_prove_rounding_happens_once_not_on_the_interval(
    live: Owner,  # noqa: F811
) -> None:
    """365/45 does not divide evenly; rounding it to 4 places before multiplying by the amount
    (an intermediate round) gives a different last digit than rounding only the final annual
    figure. The route must take the second path."""
    label = "Synthetic equivalents rounding proof"
    txn_id = _foreign_spend(live, "-9.9900", "EUR", "-18.0000", TODAY, label)
    created = from_transaction(
        transactionId=str(txn_id),
        cadence="custom",
        customIntervalDays=45,
        label=label,
        idempotencyKey=str(uuid4()),
    )
    exact_cpy = Decimal(365) / Decimal(45)
    early_rounded_cpy = exact_cpy.quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN)
    correct = (Decimal("9.99") * exact_cpy).quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN)
    wrong = (Decimal("9.99") * early_rounded_cpy).quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN)
    assert wrong != correct, "the chosen amount must actually expose early rounding"
    assert created["annualAmount"] == str(correct)
    assert created["annualAmount"] != str(wrong)


def test_equivalents_are_null_before_a_legacy_series_is_synced(live: Owner) -> None:  # noqa: F811
    """A series created before V57 (or otherwise not yet synced) has no native_amount, and the
    global execution contract says the equivalents are null exactly then, even though
    expected_amount_base is already set."""
    label = "Synthetic equivalents legacy"
    with live.conn.cursor() as cur:
        cur.execute("SELECT base_currency FROM household WHERE id = %s", (live.household_id,))
        base = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO recurring_series (household_id, idempotency_key, label, match_key,
                   cadence, expected_amount_base, currency, first_seen_on, last_seen_on)
               VALUES (%s, %s, %s, %s, 'monthly', 9, %s, %s, %s) RETURNING id""",
            (live.household_id, uuid4(), label, label.lower(), base, TODAY, TODAY),
        )
        found = cur.fetchone()
        assert found is not None
        series_id = found[0]
        cur.execute(
            """INSERT INTO recurring_series_period (household_id, series_id, started_on)
               VALUES (%s, %s, %s)""",
            (live.household_id, series_id, TODAY),
        )
    live.conn.commit()

    row = _by_id(the_recurring(), str(series_id))
    assert row["nativeAmount"] is None
    assert row["annualAmount"] is None
    assert row["monthlyAmount"] is None
    assert row["annualAmountBase"] is None
    assert row["monthlyAmountBase"] is None


# ---------------------------------------------------------------------------------------
# Summary


def test_summary_totals_equal_the_sum_of_row_figures_and_exclude_paused_ended_and_non_subscription(
    live: Owner,  # noqa: F811
) -> None:
    def a_series(label: str) -> dict[str, Any]:
        txn_id = a_spend(live, live.account_id, "-10", TODAY, "expense", description=label)
        return from_transaction(
            transactionId=str(txn_id),
            cadence="monthly",
            label=label,
            idempotencyKey=str(uuid4()),
        )

    active = a_series("Synthetic summary active")
    paused = a_series("Synthetic summary paused")
    patch_series(paused["id"], lifecycle="paused")
    ended = a_series("Synthetic summary ended")
    client().post(f"/recurring/{ended['id']}/cancel", json={}, headers=ORIGIN)
    not_subscription = a_series("Synthetic summary not a subscription")
    patch_series(not_subscription["id"], isSubscription=False)

    active_row = _by_id(the_recurring(), active["id"])
    summary = client().get("/recurring/summary", headers=ORIGIN).json()
    assert summary["currentCount"] == 1
    assert summary["monthlyBase"] == active_row["monthlyAmountBase"]
    assert summary["annualBase"] == active_row["annualAmountBase"]


def test_summary_and_by_account_add_rounded_row_figures_and_include_trial(
    live: Owner,  # noqa: F811
) -> None:
    """Two custom 45-day series of 10: each row's monthly figure is 6.7593, so the totals must be
    13.5186 — a fresh round of the unrounded sum would be 13.5185. One is a trial, which still
    counts as current."""
    ids = []
    for label, lifecycle in (
        ("Synthetic rounded sum active", "active"),
        ("Synthetic rounded sum trial", "trial"),
    ):
        series_id = _raw_series(
            live,
            label=label,
            first_seen_on=TODAY,
            last_seen_on=TODAY,
            cadence="custom",
            custom_interval_days=45,
        )
        _raw_period(live, series_id, TODAY)
        txn_id = a_spend(live, live.account_id, "-10", TODAY, "expense", description=label)
        _raw_member(live, series_id, txn_id, TODAY, "10")
        patch_series(str(series_id), lifecycle=lifecycle)
        ids.append(str(series_id))

    rows = [_by_id(the_recurring(), series_id) for series_id in ids]
    assert [r["monthlyAmountBase"] for r in rows] == ["6.7593", "6.7593"]
    summary = client().get("/recurring/summary", headers=ORIGIN).json()
    assert summary["currentCount"] == 2
    assert summary["monthlyBase"] == "13.5186"
    assert summary["annualBase"] == str(sum(Decimal(r["annualAmountBase"]) for r in rows))

    accounts = client().get("/recurring/by-account", headers=ORIGIN).json()["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["count"] == 2
    assert accounts[0]["monthlyBase"] == "13.5186"


def test_renewal_window_boundaries_in_summary_and_upcoming(live: Owner) -> None:  # noqa: F811
    at_today = _raw_series(
        live,
        label="Synthetic window at today",
        first_seen_on=TODAY - timedelta(days=30),
        last_seen_on=TODAY - timedelta(days=30),
    )
    _raw_period(live, at_today, TODAY - timedelta(days=30))

    at_edge = _raw_series(
        live, label="Synthetic window at edge", first_seen_on=TODAY, last_seen_on=TODAY
    )
    _raw_period(live, at_edge, TODAY)

    just_over = _raw_series(
        live,
        label="Synthetic window just over",
        first_seen_on=TODAY,
        last_seen_on=TODAY,
        cadence="custom",
        custom_interval_days=31,
    )
    _raw_period(live, just_over, TODAY)

    overdue_not_renewing = _raw_series(
        live,
        label="Synthetic window overdue",
        first_seen_on=TODAY - timedelta(days=40),
        last_seen_on=TODAY - timedelta(days=40),
    )
    _raw_period(live, overdue_not_renewing, TODAY - timedelta(days=40))

    summary = client().get("/recurring/summary", headers=ORIGIN).json()
    labels = {row["label"] for row in summary["upcoming"]}
    assert labels == {"Synthetic window at today", "Synthetic window at edge"}
    assert summary["renewingIn30Days"] == 2
    assert summary["currentCount"] == 4


def test_price_change_window_boundary_in_summary(live: Owner) -> None:  # noqa: F811
    within = _raw_series(
        live,
        label="Synthetic price change within",
        first_seen_on=TODAY - timedelta(days=200),
        last_seen_on=TODAY,
    )
    _raw_period(live, within, TODAY - timedelta(days=200))
    old_on, new_on = TODAY - timedelta(days=95), TODAY - timedelta(days=90)
    old_txn = a_spend(live, live.account_id, "-10", old_on, "expense", description="within")
    new_txn = a_spend(live, live.account_id, "-15", new_on, "expense", description="within")
    _raw_member(live, within, old_txn, old_on, "10")
    _raw_member(live, within, new_txn, new_on, "15")

    outside = _raw_series(
        live,
        label="Synthetic price change outside",
        first_seen_on=TODAY - timedelta(days=200),
        last_seen_on=TODAY,
    )
    _raw_period(live, outside, TODAY - timedelta(days=200))
    old_on2, new_on2 = TODAY - timedelta(days=100), TODAY - timedelta(days=91)
    old_txn2 = a_spend(live, live.account_id, "-10", old_on2, "expense", description="outside")
    new_txn2 = a_spend(live, live.account_id, "-15", new_on2, "expense", description="outside")
    _raw_member(live, outside, old_txn2, old_on2, "10")
    _raw_member(live, outside, new_txn2, new_on2, "15")

    summary = client().get("/recurring/summary", headers=ORIGIN).json()
    assert summary["priceChangesIn90Days"] == 1


# ---------------------------------------------------------------------------------------
# Detail


def test_last_12_months_window_boundary_in_detail(live: Owner) -> None:  # noqa: F811
    label = "Synthetic twelve month boundary"
    series_id = _raw_series(
        live, label=label, first_seen_on=TODAY - timedelta(days=400), last_seen_on=TODAY
    )
    _raw_period(live, series_id, TODAY - timedelta(days=400))
    account_id = _old_account(live)
    boundary = _months_back(TODAY, 12)
    on_boundary = a_spend(live, account_id, "-7", boundary, "expense", description=label)
    just_after = a_spend(
        live, account_id, "-11", boundary + timedelta(days=1), "expense", description=label
    )
    recent = a_spend(live, account_id, "-13", TODAY, "expense", description=label)
    _raw_member(live, series_id, on_boundary, boundary, "7")
    _raw_member(live, series_id, just_after, boundary + timedelta(days=1), "11")
    _raw_member(live, series_id, recent, TODAY, "13")

    detail = client().get(f"/recurring/{series_id}", headers=ORIGIN).json()
    # Strictly after the boundary (global execution contract "Windows"): the charge dated exactly
    # on it is excluded, so only the two later charges count.
    assert detail["last12MonthsBase"] == "24.0000"


def test_detail_spent_so_far_across_two_periods_and_price_history_with_one_change(
    live: Owner,  # noqa: F811
) -> None:
    label = "Synthetic detail across periods"
    series_id = _raw_series(
        live,
        label=label,
        first_seen_on=TODAY - timedelta(days=190),
        last_seen_on=TODAY - timedelta(days=30),
    )
    period1 = _raw_period(
        live,
        series_id,
        TODAY - timedelta(days=190),
        ended_on=TODAY - timedelta(days=100),
        end_reason="cancelled",
    )
    period2 = _raw_period(live, series_id, TODAY - timedelta(days=90))

    dates_amounts = [
        (TODAY - timedelta(days=190), "10"),
        (TODAY - timedelta(days=160), "10"),
        (TODAY - timedelta(days=60), "15"),
        (TODAY - timedelta(days=30), "15"),
    ]
    for on, amount in dates_amounts:
        txn_id = a_spend(live, live.account_id, f"-{amount}", on, "expense", description=label)
        _raw_member(live, series_id, txn_id, on, amount)

    detail = client().get(f"/recurring/{series_id}", headers=ORIGIN).json()
    assert detail["spentSoFarBase"] == "50.0000"

    periods = detail["periods"]
    assert [p["id"] for p in periods] == [str(period1), str(period2)]
    assert periods[0]["chargeCount"] == 2
    assert periods[0]["spentBase"] == "20.0000"
    assert periods[1]["chargeCount"] == 2
    assert periods[1]["spentBase"] == "30.0000"

    history = detail["priceHistory"]
    assert [h["amount"] for h in history] == ["10.0000", "15.0000"]
    assert history[0]["observedOn"] == str(TODAY - timedelta(days=190))
    assert history[1]["observedOn"] == str(TODAY - timedelta(days=60))


def test_each_charge_reports_its_period_including_one_that_moved_a_start_back(
    live: Owner,  # noqa: F811
) -> None:
    label = "Synthetic charge period assignment"
    series_id = _raw_series(
        live, label=label, first_seen_on=TODAY - timedelta(days=70), last_seen_on=TODAY
    )
    # period1's own started_on sits at -70, pulled back by an earlier charge that joined after the
    # period first opened — the same move Extend makes (Task 5) — then cancelled at -40.
    period1 = _raw_period(
        live,
        series_id,
        TODAY - timedelta(days=70),
        ended_on=TODAY - timedelta(days=40),
        end_reason="cancelled",
    )
    period2 = _raw_period(live, series_id, TODAY - timedelta(days=20))

    early = a_spend(
        live, live.account_id, "-9", TODAY - timedelta(days=70), "expense", description=label
    )
    mid = a_spend(
        live, live.account_id, "-9", TODAY - timedelta(days=50), "expense", description=label
    )
    late = a_spend(
        live, live.account_id, "-9", TODAY - timedelta(days=10), "expense", description=label
    )
    for txn_id, on in (
        (early, TODAY - timedelta(days=70)),
        (mid, TODAY - timedelta(days=50)),
        (late, TODAY - timedelta(days=10)),
    ):
        _raw_member(live, series_id, txn_id, on, "9")

    detail = client().get(f"/recurring/{series_id}", headers=ORIGIN).json()
    by_txn = {c["transactionId"]: c["periodId"] for c in detail["charges"]}
    assert by_txn[str(early)] == str(period1)
    assert by_txn[str(mid)] == str(period1)
    assert by_txn[str(late)] == str(period2)


def test_a_charge_later_excluded_from_reporting_drops_out_of_the_sums(live: Owner) -> None:  # noqa: F811
    label = "Synthetic excluded charge"
    series_id = _raw_series(
        live, label=label, first_seen_on=TODAY - timedelta(days=60), last_seen_on=TODAY
    )
    _raw_period(live, series_id, TODAY - timedelta(days=60))
    kept = a_spend(
        live, live.account_id, "-10", TODAY - timedelta(days=60), "expense", description=label
    )
    dropped = a_spend(
        live, live.account_id, "-20", TODAY - timedelta(days=10), "expense", description=label
    )
    _raw_member(live, series_id, kept, TODAY - timedelta(days=60), "10")
    _raw_member(live, series_id, dropped, TODAY - timedelta(days=10), "20")

    before = client().get(f"/recurring/{series_id}", headers=ORIGIN).json()
    assert before["spentSoFarBase"] == "30.0000"
    assert len(before["charges"]) == 2

    with live.conn.cursor() as cur:
        cur.execute("UPDATE transaction SET is_excluded = true WHERE id = %s", (dropped,))
    live.conn.commit()

    after = client().get(f"/recurring/{series_id}", headers=ORIGIN).json()
    assert after["spentSoFarBase"] == "10.0000"
    assert len(after["charges"]) == 1


# ---------------------------------------------------------------------------------------
# By account, payment channels, masked identifiers, tenant boundary


def test_by_account_groups_current_subscriptions_and_excludes_a_non_subscription(
    live: Owner,  # noqa: F811
) -> None:
    label_sub = "Synthetic by-account subscription"
    label_not_sub = "Synthetic by-account not subscription"
    sub_series = _raw_series(live, label=label_sub, first_seen_on=TODAY, last_seen_on=TODAY)
    _raw_period(live, sub_series, TODAY)
    not_sub_series = _raw_series(
        live, label=label_not_sub, first_seen_on=TODAY, last_seen_on=TODAY, is_subscription=False
    )
    _raw_period(live, not_sub_series, TODAY)
    sub_txn = a_spend(live, live.account_id, "-10", TODAY, "expense", description=label_sub)
    not_sub_txn = a_spend(live, live.account_id, "-10", TODAY, "expense", description=label_not_sub)
    _raw_member(live, sub_series, sub_txn, TODAY, "10")
    _raw_member(live, not_sub_series, not_sub_txn, TODAY, "10")

    accounts = client().get("/recurring/by-account", headers=ORIGIN).json()["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["accountId"] == str(live.account_id)
    assert accounts[0]["labels"] == [label_sub]
    assert accounts[0]["count"] == 1


def test_paying_account_masked_identifier_chosen_by_kind_and_never_the_unmasked_value(
    live: Owner,  # noqa: F811
) -> None:
    """`live.account_id` already carries a `bank_account` identifier (the fixture's own); adding a
    `card` one and expecting it back proves the kind priority, not just "some identifier"."""
    label = "Synthetic masked identifier"
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account_identifier (household_id, account_id, kind, masked_value,
                   visible_suffix, full_value, normalized_value, provenance)
               VALUES (%s, %s, 'card', '**** 4242', '4242', %s, %s, 'owner_entered')""",
            (live.household_id, live.account_id, CARD_NUMBER, CARD_NUMBER),
        )
    live.conn.commit()

    series_id = _raw_series(live, label=label, first_seen_on=TODAY, last_seen_on=TODAY)
    _raw_period(live, series_id, TODAY)
    txn_id = a_spend(live, live.account_id, "-10", TODAY, "expense", description=label)
    _raw_member(live, series_id, txn_id, TODAY, "10")

    row = _by_id(the_recurring(), str(series_id))
    assert row["payingAccountMasked"] == "**** 4242"
    assert row["payingAccountId"] == str(live.account_id)

    response_text = client().get("/recurring", headers=ORIGIN).text
    assert CARD_NUMBER not in response_text


def test_patch_refuses_another_households_payment_channel(live: Owner) -> None:  # noqa: F811
    label = "Synthetic patch payment channel refusal"
    theirs, _their_account = _another_household(live)
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO payment_channel (household_id, name) VALUES (%s, %s) RETURNING id",
            (theirs, "Synthetic foreign channel"),
        )
        found = cur.fetchone()
        assert found is not None
        foreign_channel = found[0]
        cur.execute(
            "SELECT id FROM payment_channel WHERE household_id IS NULL ORDER BY sort_order LIMIT 1"
        )
        seed_row = cur.fetchone()
        assert seed_row is not None
        seed_channel = seed_row[0]
    live.conn.commit()

    a_monthly_group(live, description=label, amount="-10")
    refresh()
    row = _first(the_recurring(), label)
    series_id = row["id"]

    refused = client().patch(
        f"/recurring/{series_id}", json={"paymentChannelId": str(foreign_channel)}, headers=ORIGIN
    )
    assert refused.status_code == 422, refused.text

    accepted = patch_series(series_id, paymentChannelId=str(seed_channel))
    assert accepted["paymentChannelId"] == str(seed_channel)
    assert accepted["paymentChannelName"] is not None

    # A foreign reference written behind the route's back never leaks the other household's name.
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE recurring_series SET payment_channel_id = %s WHERE id = %s",
            (foreign_channel, series_id),
        )
    live.conn.commit()
    assert _by_id(the_recurring(), series_id)["paymentChannelName"] is None


def test_payment_channels_lists_seed_and_own_active_channels(live: Owner) -> None:  # noqa: F811
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO payment_channel (household_id, name, sort_order) VALUES (%s, %s, 5)",
            (live.household_id, "Synthetic household channel"),
        )
        cur.execute(
            "INSERT INTO payment_channel (household_id, name, is_active) VALUES (%s, %s, false)",
            (live.household_id, "Synthetic inactive channel"),
        )
    live.conn.commit()
    theirs, _their_account = _another_household(live)
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO payment_channel (household_id, name) VALUES (%s, %s)",
            (theirs, "Synthetic foreign household channel"),
        )
    live.conn.commit()

    names = [
        c["name"] for c in client().get("/payment-channels", headers=ORIGIN).json()["channels"]
    ]
    assert "Direct card" in names
    assert "Synthetic household channel" in names
    assert "Synthetic inactive channel" not in names
    assert "Synthetic foreign household channel" not in names


def test_new_routes_refuse_another_household(live: Owner) -> None:  # noqa: F811
    label = "Synthetic new routes foreign refusal"
    a_monthly_group(live, description=label, amount="-10")
    refresh()
    row = _first(the_recurring(), label)
    series_id = row["id"]

    theirs, _their_account = _another_household(live)
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(),
        household_id=theirs,
        session_id=uuid4(),
        email="other-figures-owner@example.invalid",
    )
    try:
        assert client().get(f"/recurring/{series_id}", headers=ORIGIN).status_code == 404
        summary = client().get("/recurring/summary", headers=ORIGIN).json()
        assert summary["currentCount"] == 0
        by_account = client().get("/recurring/by-account", headers=ORIGIN).json()
        assert by_account["accounts"] == []
        names = [
            c["name"] for c in client().get("/payment-channels", headers=ORIGIN).json()["channels"]
        ]
        assert label not in names
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner


def test_new_routes_require_owner_session() -> None:
    app.dependency_overrides.clear()
    assert client().get(f"/recurring/{uuid4()}", headers=ORIGIN).status_code == 401
    assert client().get("/recurring/summary", headers=ORIGIN).status_code == 401
    assert client().get("/recurring/by-account", headers=ORIGIN).status_code == 401
    assert client().get("/payment-channels", headers=ORIGIN).status_code == 401
