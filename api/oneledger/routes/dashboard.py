"""Slice 1.4 task 4b2: the dashboard — net worth, this month, the trend and the cards due.

`GET /dashboard` answers in one payload. Its balance-sheet half reads the one valuation source
selected for each account plus `card_statement`; its flow half reads `v_transaction_reportable`
and nothing else.

**Net worth is one SUM of signed values** (ADR-0010) — ledger-mode signed balances, holding values
or one frozen snapshot, never two sources for one account. Assets are positive and liabilities
negative; the asset, liability and liquid figures are FILTERs on the same PostgreSQL sum. `asOf`
selects the valuation period, event-derived units, accepted non-future price, snapshot, rates and
card cycles. A ledger-mode account still contributes its current balance, so a complete historical
balance sheet remains the planned Phase 2.2 net-worth history's.

**Which rate, and how it is labelled (Invariant 7).** The date's own row when the table holds one —
itself labelled carried over a weekend, with the date it really comes from. The table is filled
forward only as far as its last fetch, so past it the dashboard fills forward the same way the table
fills its weekends, never interpolating, and labels the rate carried with its real date. A held
currency with no rate at or before the date makes that reporting currency's net worth **null**,
naming the currency and the date — never a partial sum, which would be a smaller, confident, wrong
number. This route fetches no rate: reads write nothing.

**The composition reuses that one selection (Phase 4.8).** `positionComposition` groups exactly
the values net worth resolved into six stable buckets; it opens no second valuation model and
reads no account a second way. Shares are absolute over the absolute included total, so a
liability is a visible part of the position rather than a cancellation of an asset, and a missing
reporting value nulls that reporting currency for the whole composition rather than shrinking it.

**Flows are frozen (I3, I5).** This month and the eleven before it sum `amount_base` and
`amount_secondary` as each row froze them on its own day — never the base table, never a rate.
Income is the `income` rows; spending is every other reportable row with its sign turned, so a
refund lowers spending rather than counting as income (`classification.html`); net is one SUM of
the lot. The savings rate is net over income, null when income is not positive (`finance-os.html`
F02).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..commitments import commitment_progress
from ..coverage import month_end, months_from_anchor
from ..db import connect, scope_to_owner
from ..investments.positions import current_rate_pair, read_positions
from ..money import _ZERO, CENT, _out, chart_ratios, translate
from .coverage import CoverageRow, coverage_rows

router = APIRouter(tags=["dashboard"])


class RateUsed(BaseModel):
    currency: str
    rateToBase: str | None
    rateToSecondary: str | None
    # The date the rate is really from, and whether that is not the day being viewed.
    rateDate: date | None
    carried: bool


class MissingRate(BaseModel):
    currency: str
    reportingCurrency: str
    asOf: date


class InvestmentCoverage(BaseModel):
    accountId: UUID
    holdingId: UUID | None
    valuationMode: Literal["holdings", "snapshot"]
    status: Literal[
        "current_price",
        "stale_price",
        "missing_price",
        "future_price_only",
        "outside_supported_range",
        "current_snapshot",
        "carried_snapshot",
        "missing_snapshot",
    ]
    valueDate: date | None
    carried: bool
    ageDays: int | None
    method: Literal["owner_entered", "market_quote", "indexed", "statement"] | None
    confidence: Literal["high", "medium", "low"] | None


class NetWorth(BaseModel):
    asOf: date
    baseCurrency: str
    secondaryCurrency: str
    totalBase: str | None
    totalSecondary: str | None
    assetsBase: str | None
    assetsSecondary: str | None
    liabilitiesBase: str | None
    liabilitiesSecondary: str | None
    liquidBase: str | None
    liquidSecondary: str | None
    rates: list[RateUsed]
    missing: list[MissingRate]
    investmentCoverage: list[InvestmentCoverage]


BucketKey = Literal["cash", "investments", "property", "retirement", "other", "liabilities"]

# The composition's stable order and membership (Phase 4.8 spec, "Dashboard position
# composition"). An included liability lands in `liabilities` whatever its type, so the two
# residual buckets are decided by `account_class` rather than by listing every liability type
# here — a new liability type cannot silently become an asset bucket.
_BUCKETS: tuple[tuple[BucketKey, str, frozenset[str]], ...] = (
    (
        "cash",
        "Cash and equivalents",
        frozenset({"cash", "current", "savings", "salary", "fixed_deposit", "recurring_deposit"}),
    ),
    (
        "investments",
        "Investments",
        frozenset({"brokerage", "mutual_fund_folio", "crypto", "precious_metal"}),
    ),
    ("property", "Property", frozenset({"property"})),
    ("retirement", "Retirement", frozenset({"retirement"})),
    ("other", "Other assets", frozenset()),
    ("liabilities", "Liabilities", frozenset()),
)

_PERCENT_PLACES = Decimal("0.0001")


class PositionCompositionMissing(BaseModel):
    accountId: UUID
    currency: str


class PositionCompositionRow(BaseModel):
    key: BucketKey
    label: str
    # Signed, as the ledger holds it: a liability row stays negative (ADR-0010).
    valueBase: str | None
    valueSecondary: str | None
    # The row's absolute share of the absolute included total, so a liability is visible instead
    # of cancelling an asset. Human-readable at four places: "12.5000" is 12.5%.
    sharePercentBase: str | None
    sharePercentSecondary: str | None
    # The same ratio on the inclusive 0–10,000 integer scale — always non-negative.
    chartCoordinateBase: int | None
    chartCoordinateSecondary: int | None


class PositionComposition(BaseModel):
    rows: list[PositionCompositionRow]
    missing: list[PositionCompositionMissing]


class CardDue(BaseModel):
    accountId: UUID
    accountName: str
    currency: str
    periodEnd: date
    statementBalance: str
    minimumDue: str | None
    paymentDueDate: date
    daysRemaining: int
    isPaidInFull: bool | None
    # statement_balance ÷ credit_limit at statement date, human-readable: 22.5000 is 22.5%.
    utilisationPct: str | None


class MonthFlows(BaseModel):
    # The first day of the month the figures belong to.
    month: date
    incomeBase: str
    incomeSecondary: str
    spendingBase: str
    spendingSecondary: str
    netBase: str
    netSecondary: str
    # net ÷ income, human-readable: 15.2500 is 15.25%. Null when income is not positive.
    savingsRatePctBase: str | None
    savingsRatePctSecondary: str | None


class YearToDate(BaseModel):
    # Calendar year of `asOf`, 1 January through `asOf` inclusive.
    yearStart: date
    asOf: date
    incomeBase: str
    incomeSecondary: str
    spendingBase: str
    spendingSecondary: str
    netBase: str
    netSecondary: str
    savingsRatePctBase: str | None
    savingsRatePctSecondary: str | None
    investedBase: str
    investedSecondary: str


class FlowEvidence(BaseModel):
    month: date
    status: Literal["complete", "unknown"]
    incomeBaseCoordinate: int | None
    spendingBaseCoordinate: int | None
    netBaseCoordinate: int | None
    incomeSecondaryCoordinate: int | None
    spendingSecondaryCoordinate: int | None
    netSecondaryCoordinate: int | None


class UpcomingCommitment(BaseModel):
    commitmentId: UUID
    label: str
    dueDate: date
    nativeAmount: str
    nativeCurrency: str
    remainingBase: str | None
    remainingSecondary: str | None


class UpcomingCommitmentsSummary(BaseModel):
    asOf: date
    through: date
    count: int
    totalBase: str | None
    totalSecondary: str | None
    missingBaseCurrencies: list[str]
    missingSecondaryCurrencies: list[str]
    next: UpcomingCommitment | None
    items: list[UpcomingCommitment]


class DashboardResponse(BaseModel):
    netWorth: NetWorth
    thisMonth: MonthFlows
    trend: list[MonthFlows]
    hasRecordedFlows: bool
    flowEvidence: list[FlowEvidence]
    cardsDue: list[CardDue]
    yearToDate: YearToDate
    upcomingCommitments: UpcomingCommitmentsSummary
    positionComposition: PositionComposition


_COUNTED = "a.household_id = %s AND a.is_active AND a.include_in_net_worth"

# The valuation period covering `a.id` at a given date: the active period effective then, or —
# for a date before any period started — the account's first period. Shared with
# `routes/plans.py`'s forecast (spec "Forecast computation" rule 1) so both read the same
# account's mode the same way; two `%s` placeholders (the date, twice) precede the household
# filter wherever this is embedded.
VALUATION_PERIOD_FOR_ACCOUNT = """coalesce(
                    (SELECT active.id FROM account_valuation_period active
                      WHERE active.account_id = a.id AND %s >= active.effective_from
                        AND (active.effective_to IS NULL OR %s <= active.effective_to)
                      ORDER BY active.effective_from DESC LIMIT 1),
                    (SELECT initial.id FROM account_valuation_period initial
                      WHERE initial.account_id = a.id
                      ORDER BY initial.effective_from LIMIT 1))"""

NET_WORTH = """
    WITH values (account_class, is_liquid, value_base, value_secondary) AS (
        SELECT * FROM unnest(%s::account_class[], %s::boolean[], %s::numeric[], %s::numeric[])
    )
    SELECT coalesce(round(sum(value_base), 4), 0),
           coalesce(round(sum(value_secondary), 4), 0),
           coalesce(round(sum(value_base) FILTER (WHERE account_class = 'asset'), 4), 0),
           coalesce(round(sum(value_secondary) FILTER (WHERE account_class = 'asset'), 4), 0),
           coalesce(round(sum(value_base) FILTER (WHERE account_class = 'liability'), 4), 0),
           coalesce(round(sum(value_secondary) FILTER (WHERE account_class = 'liability'), 4), 0),
           coalesce(round(sum(value_base) FILTER (WHERE is_liquid), 4), 0),
           coalesce(round(sum(value_secondary) FILTER (WHERE is_liquid), 4), 0)
      FROM values"""

# The latest cycle per card known as of the date. Days and utilisation are computed here, and
# utilisation is absent when the cycle states no limit — NULLIF, so no CASE is needed.
CARDS_DUE = """
    SELECT * FROM (
        SELECT DISTINCT ON (c.account_id)
               c.account_id, a.name, c.currency, c.period_end, c.statement_balance,
               c.minimum_due, c.payment_due_date, c.payment_due_date - %s::date,
               c.is_paid_in_full,
               round(c.statement_balance / NULLIF(c.credit_limit, 0) * 100, 4)
          FROM card_statement c JOIN account a ON a.id = c.account_id
         WHERE c.household_id = %s AND a.is_active AND c.period_end <= %s
         ORDER BY c.account_id, c.period_end DESC
    ) latest
    ORDER BY payment_due_date, name"""


# The month of the as-of date and the eleven before it, through the as-of date, from frozen
# amounts only. A month with no rows is zeros and no savings rate; GREATEST and NULLIF make the
# rate null wherever income is not positive, so no CASE is needed.
FLOWS = """
    WITH months AS (
        SELECT generate_series(date_trunc('month', %(as_of)s::timestamp) - interval '11 months',
                               date_trunc('month', %(as_of)s::timestamp),
                               interval '1 month')::date AS month
    ), flows AS (
        SELECT date_trunc('month', booked_date::timestamp)::date AS month,
               sum(amount_base) FILTER (WHERE txn_type = 'income') AS income_base,
               sum(amount_secondary) FILTER (WHERE txn_type = 'income') AS income_secondary,
               -sum(amount_base) FILTER (WHERE txn_type <> 'income') AS spending_base,
               -sum(amount_secondary) FILTER (WHERE txn_type <> 'income') AS spending_secondary,
               sum(amount_base) AS net_base,
               sum(amount_secondary) AS net_secondary,
               count(*) AS recorded_count
          FROM v_transaction_reportable
         WHERE household_id = %(household)s
           AND booked_date >= date_trunc('month', %(as_of)s::timestamp) - interval '11 months'
           AND booked_date <= %(as_of)s
         GROUP BY 1
    )
    SELECT m.month,
           coalesce(f.income_base, 0.0000), coalesce(f.income_secondary, 0.0000),
           coalesce(f.spending_base, 0.0000), coalesce(f.spending_secondary, 0.0000),
           coalesce(f.net_base, 0.0000), coalesce(f.net_secondary, 0.0000),
           round(f.net_base / NULLIF(GREATEST(f.income_base, 0), 0) * 100, 4),
           round(f.net_secondary / NULLIF(GREATEST(f.income_secondary, 0), 0) * 100, 4),
           coalesce(f.recorded_count, 0)
      FROM months m LEFT JOIN flows f USING (month)
     ORDER BY m.month"""


# The calendar year of `asOf` through `asOf`, same classification as FLOWS, plus investment
# contributions from active, counted accounts over the same window — active (not corrected) rows
# only, so a corrected purchase counts once, via its replacement.
YEAR_TO_DATE = """
    WITH flows AS (
        SELECT sum(amount_base) FILTER (WHERE txn_type = 'income') AS income_base,
               sum(amount_secondary) FILTER (WHERE txn_type = 'income') AS income_secondary,
               -sum(amount_base) FILTER (WHERE txn_type <> 'income') AS spending_base,
               -sum(amount_secondary) FILTER (WHERE txn_type <> 'income') AS spending_secondary,
               sum(amount_base) AS net_base,
               sum(amount_secondary) AS net_secondary
          FROM v_transaction_reportable
         WHERE household_id = %(household)s
           AND booked_date >= date_trunc('year', %(as_of)s::timestamp)
           AND booked_date <= %(as_of)s
    ), invested AS (
        SELECT sum(-e.net_amount_base) AS invested_base,
               sum(-e.net_amount_secondary) AS invested_secondary
          FROM investment_transaction e
          JOIN account a ON a.household_id = e.household_id AND a.id = e.account_id
         WHERE e.household_id = %(household)s AND a.is_active AND a.include_in_net_worth
           AND e.event_kind IN ('purchase', 'recurring_purchase', 'rights')
           AND e.event_date >= date_trunc('year', %(as_of)s::timestamp)
           AND e.event_date <= %(as_of)s
           AND NOT EXISTS (
               SELECT 1 FROM investment_transaction c WHERE c.corrects_transaction_id = e.id
           )
    )
    SELECT coalesce(f.income_base, 0.0000), coalesce(f.income_secondary, 0.0000),
           coalesce(f.spending_base, 0.0000), coalesce(f.spending_secondary, 0.0000),
           coalesce(f.net_base, 0.0000), coalesce(f.net_secondary, 0.0000),
           round(f.net_base / NULLIF(GREATEST(f.income_base, 0), 0) * 100, 4),
           round(f.net_secondary / NULLIF(GREATEST(f.income_secondary, 0), 0) * 100, 4),
           coalesce(round(i.invested_base, 4), 0.0000),
           coalesce(round(i.invested_secondary, 4), 0.0000)
      FROM flows f, invested i"""


UPCOMING_COMMITMENTS = """
    SELECT c.id, c.label, c.amount, c.currency, c.next_due_date
      FROM commitment c
     WHERE c.household_id = %s
       AND c.archived_at IS NULL
       AND c.next_due_date >= %s
       AND c.next_due_date <= %s
     ORDER BY c.next_due_date, c.label, c.id"""


def _upcoming_commitments(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    base: str,
    secondary: str,
    as_of: date,
) -> UpcomingCommitmentsSummary:
    through = as_of + timedelta(days=30)
    cur.execute(UPCOMING_COMMITMENTS, (household_id, as_of, through))
    rows = cur.fetchall()
    progress = commitment_progress(
        cur,
        household_id,
        as_of,
        [(row[0], None, row[4], Decimal(row[2])) for row in rows],
    )
    rates: dict[str, tuple[Decimal | None, Decimal | None]] = {}
    missing_base: set[str] = set()
    missing_secondary: set[str] = set()
    total_base = Decimal("0.0000")
    total_secondary = Decimal("0.0000")
    items: list[UpcomingCommitment] = []

    for commitment_id, label, _amount, currency_raw, due_date in rows:
        commitment_id = UUID(str(commitment_id))
        currency = str(currency_raw).strip()
        remaining_raw = progress[commitment_id][3]
        assert remaining_raw is not None
        remaining = Decimal(remaining_raw)
        if currency not in rates:
            pair = current_rate_pair(cur, currency, base, secondary, as_of)
            rates[currency] = (
                None if pair.to_base is None else pair.to_base.rate,
                None if pair.to_secondary is None else pair.to_secondary.rate,
            )
        rate_base, rate_secondary = rates[currency]
        remaining_base = None if rate_base is None else translate(remaining, rate_base)
        remaining_secondary = (
            None if rate_secondary is None else translate(remaining, rate_secondary)
        )
        if remaining_base is None:
            missing_base.add(currency)
        else:
            total_base += remaining_base
        if remaining_secondary is None:
            missing_secondary.add(currency)
        else:
            total_secondary += remaining_secondary
        items.append(
            UpcomingCommitment(
                commitmentId=commitment_id,
                label=label,
                dueDate=due_date,
                nativeAmount=str(remaining),
                nativeCurrency=currency,
                remainingBase=_out(remaining_base),
                remainingSecondary=_out(remaining_secondary),
            )
        )

    return UpcomingCommitmentsSummary(
        asOf=as_of,
        through=through,
        count=len(items),
        totalBase=None if missing_base else str(total_base),
        totalSecondary=None if missing_secondary else str(total_secondary),
        missingBaseCurrencies=sorted(missing_base),
        missingSecondaryCurrencies=sorted(missing_secondary),
        next=items[0] if items else None,
        items=items,
    )


def flow_evidence(
    trend: Sequence[MonthFlows],
    coverage_months: Sequence[date],
    coverage: Sequence[CoverageRow],
    as_of: date,
) -> list[FlowEvidence]:
    """Plot only completed statement periods. Frozen flows remain available in the detailed
    table, but an unverified or still-open month cannot imply a complete household total."""
    positions = {month: index for index, month in enumerate(coverage_months)}
    complete: set[date] = set()
    account_rows = [row for row in coverage if row.subject == "account"]
    for item in trend:
        index = positions.get(item.month)
        if index is None or month_end(item.month) > as_of:
            continue
        applicable = [
            row.months[index].status
            for row in account_rows
            if index < len(row.months) and row.months[index].status != "outside_account_life"
        ]
        if applicable and all(status == "reconciled" for status in applicable):
            complete.add(item.month)

    def coordinates(suffix: Literal["Base", "Secondary"]) -> dict[tuple[date, str], int]:
        values = {
            (item.month, field): Decimal(getattr(item, f"{field}{suffix}"))
            for item in trend
            if item.month in complete
            for field in ("income", "spending", "net")
        }
        return {key: int(Decimal(value) * 10000) for key, value in chart_ratios(values).items()}

    base = coordinates("Base")
    secondary = coordinates("Secondary")
    return [
        FlowEvidence(
            month=item.month,
            status="complete" if item.month in complete else "unknown",
            incomeBaseCoordinate=base.get((item.month, "income")),
            spendingBaseCoordinate=base.get((item.month, "spending")),
            netBaseCoordinate=base.get((item.month, "net")),
            incomeSecondaryCoordinate=secondary.get((item.month, "income")),
            spendingSecondaryCoordinate=secondary.get((item.month, "spending")),
            netSecondaryCoordinate=secondary.get((item.month, "net")),
        )
        for item in trend
    ]


def _bucket_for(account_class: str, account_type: str) -> BucketKey:
    if account_class == "liability":
        return "liabilities"
    for key, _, types in _BUCKETS:
        if account_type in types:
            return key
    return "other"


def position_composition(
    facts: Sequence[tuple[UUID, str, str, Decimal | None, Decimal | None]],
    base: str,
    secondary: str,
) -> PositionComposition:
    """The included position grouped into the spec's six stable buckets from exactly the facts
    net worth already resolved — the same one-source-per-account selection, never a second
    valuation model. A share is the bucket's **absolute** value over the sum of absolute bucket
    values, so a liability is a visible part of the position instead of cancelling an asset,
    while `value*` keeps the ledger's sign. A missing reporting value nulls that reporting
    currency for the whole composition and names the account, exactly as net worth refuses a
    partial sum (I7): a smaller, confident, wrong share is worse than an absent one.

    `facts` is (accountId, account_class, account_type, valueBase, valueSecondary); a
    holdings-mode account contributes one fact per holding, so nothing is counted twice.
    """
    order = {key: index for index, (key, _, _) in enumerate(_BUCKETS)}
    labels = {key: label for key, label, _ in _BUCKETS}
    totals: dict[BucketKey, tuple[Decimal, Decimal]] = {}
    missing_base: set[UUID] = set()
    missing_secondary: set[UUID] = set()

    for account_id, account_class, account_type, value_base, value_secondary in facts:
        key = _bucket_for(account_class, account_type)
        running_base, running_secondary = totals.get(key, (_ZERO, _ZERO))
        if value_base is None:
            missing_base.add(account_id)
        else:
            running_base += value_base
        if value_secondary is None:
            missing_secondary.add(account_id)
        else:
            running_secondary += value_secondary
        totals[key] = (running_base, running_secondary)

    keys = sorted(totals, key=lambda key: order[key])
    base_whole = not missing_base
    secondary_whole = not missing_secondary
    base_denominator = sum((abs(totals[key][0]) for key in keys), _ZERO)
    secondary_denominator = sum((abs(totals[key][1]) for key in keys), _ZERO)

    def share(value: Decimal, denominator: Decimal) -> tuple[str, int]:
        if denominator == 0:
            return "0.0000", 0
        ratio = abs(value) / denominator
        return (
            str((ratio * 100).quantize(_PERCENT_PLACES, rounding=ROUND_HALF_EVEN)),
            int((ratio * 10000).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)),
        )

    rows: list[PositionCompositionRow] = []
    for key in keys:
        value_base = totals[key][0].quantize(CENT, rounding=ROUND_HALF_EVEN)
        value_secondary = totals[key][1].quantize(CENT, rounding=ROUND_HALF_EVEN)
        base_share = share(value_base, base_denominator) if base_whole else None
        secondary_share = share(value_secondary, secondary_denominator) if secondary_whole else None
        rows.append(
            PositionCompositionRow(
                key=key,
                label=labels[key],
                valueBase=str(value_base) if base_whole else None,
                valueSecondary=str(value_secondary) if secondary_whole else None,
                sharePercentBase=None if base_share is None else base_share[0],
                sharePercentSecondary=None if secondary_share is None else secondary_share[0],
                chartCoordinateBase=None if base_share is None else base_share[1],
                chartCoordinateSecondary=(None if secondary_share is None else secondary_share[1]),
            )
        )

    return PositionComposition(
        rows=rows,
        missing=sorted(
            [
                PositionCompositionMissing(accountId=account_id, currency=currency)
                for currency, absent in ((base, missing_base), (secondary, missing_secondary))
                for account_id in absent
            ],
            key=lambda item: (str(item.accountId), item.currency),
        ),
    )


def _net_worth(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, as_of: date
) -> tuple[NetWorth, PositionComposition]:
    cur.execute(
        f"""SELECT a.id, a.account_class::text, a.is_liquid, a.currency, a.current_balance,
                   period.valuation_mode::text, period.effective_from, a.account_type::text
              FROM account a
              JOIN account_valuation_period period
                ON period.id = {VALUATION_PERIOD_FOR_ACCOUNT}
             WHERE {_COUNTED} ORDER BY a.id""",
        (as_of, as_of, household_id),
    )
    accounts = cur.fetchall()
    account_by_id = {row[0]: row for row in accounts}
    positions = read_positions(cur, household_id, base, secondary, as_of, included_only=True)
    held = sorted(
        {
            *(str(row[3]).strip() for row in accounts if row[5] == "ledger_balance"),
            *(position.currency for position in positions),
        }
    )
    rates: list[RateUsed] = []
    missing: list[MissingRate] = []
    rate_pairs: dict[str, tuple[Decimal | None, Decimal | None]] = {}
    for currency in held:
        pair = current_rate_pair(cur, currency, base, secondary, as_of)
        base_leg = None if pair.to_base is None else pair.to_base.rate
        secondary_leg = None if pair.to_secondary is None else pair.to_secondary.rate
        dated = [leg for leg in (pair.to_base, pair.to_secondary) if leg is not None]
        if pair.to_base is None:
            missing.append(MissingRate(currency=currency, reportingCurrency=base, asOf=as_of))
        if pair.to_secondary is None:
            missing.append(MissingRate(currency=currency, reportingCurrency=secondary, asOf=as_of))
        rates.append(
            RateUsed(
                currency=currency,
                rateToBase=_out(base_leg),
                rateToSecondary=_out(secondary_leg),
                rateDate=min((leg.real_date for leg in dated), default=None),
                carried=any(leg.carried for leg in dated),
            )
        )
        rate_pairs[currency] = (base_leg, secondary_leg)

    classes: list[str] = []
    liquidity: list[bool] = []
    values_base: list[Decimal | None] = []
    values_secondary: list[Decimal | None] = []
    coverage: list[InvestmentCoverage] = []
    # The same resolved facts the sum below reads, kept with their account identity and type so
    # the composition groups them without resolving any account a second way.
    facts: list[tuple[UUID, str, str, Decimal | None, Decimal | None]] = []

    def counted(
        account_id: UUID,
        account_class: str,
        account_type: str,
        liquid: bool,
        value_base: Decimal | None,
        value_secondary: Decimal | None,
    ) -> None:
        classes.append(account_class)
        liquidity.append(liquid)
        values_base.append(value_base)
        values_secondary.append(value_secondary)
        facts.append((account_id, account_class, account_type, value_base, value_secondary))

    for (
        account_id,
        account_class,
        liquid,
        currency_raw,
        balance,
        mode,
        effective_from,
        account_type,
    ) in accounts:
        currency = str(currency_raw).strip()
        if mode == "ledger_balance":
            rate_base, rate_secondary = rate_pairs[currency]
            counted(
                account_id,
                account_class,
                account_type,
                bool(liquid),
                None if rate_base is None else translate(Decimal(balance), rate_base),
                None if rate_secondary is None else translate(Decimal(balance), rate_secondary),
            )
        elif mode == "snapshot":
            cur.execute(
                """SELECT as_of_date, value_base, value_secondary,
                          method::text, confidence::text
                     FROM valuation_snapshot
                    WHERE household_id = %s AND account_id = %s
                      AND as_of_date >= %s AND as_of_date <= %s
                    ORDER BY as_of_date DESC, created_at DESC, id DESC LIMIT 1""",
                (household_id, account_id, effective_from, as_of),
            )
            snapshot = cur.fetchone()
            counted(
                account_id,
                account_class,
                account_type,
                bool(liquid),
                None if snapshot is None else Decimal(snapshot[1]),
                None if snapshot is None else Decimal(snapshot[2]),
            )
            value_date = None if snapshot is None else snapshot[0]
            coverage.append(
                InvestmentCoverage(
                    accountId=account_id,
                    holdingId=None,
                    valuationMode="snapshot",
                    status=(
                        "missing_snapshot"
                        if value_date is None
                        else "current_snapshot"
                        if value_date == as_of
                        else "carried_snapshot"
                    ),
                    valueDate=value_date,
                    carried=value_date is not None and value_date < as_of,
                    ageDays=None if value_date is None else (as_of - value_date).days,
                    method=None if snapshot is None else snapshot[3],
                    confidence=None if snapshot is None else snapshot[4],
                )
            )

    price_coverage = {
        "current": "current_price",
        "stale": "stale_price",
        "missing": "missing_price",
        "future_price_only": "future_price_only",
    }
    for position in positions:
        account = account_by_id[position.accountId]
        counted(
            position.accountId,
            str(account[1]),
            str(account[7]),
            bool(account[2]),
            None if position.currentValueBase is None else Decimal(position.currentValueBase),
            (
                None
                if position.currentValueSecondary is None
                else Decimal(position.currentValueSecondary)
            ),
        )
        coverage.append(
            InvestmentCoverage(
                accountId=position.accountId,
                holdingId=position.holdingId,
                valuationMode="holdings",
                status=(
                    "outside_supported_range"
                    if position.valueStatus == "outside_supported_range"
                    else price_coverage[position.priceStatus]  # type: ignore[arg-type]
                ),
                valueDate=position.priceDate,
                carried=position.priceDate is not None and position.priceDate < as_of,
                ageDays=(None if position.priceDate is None else (as_of - position.priceDate).days),
                method=None,
                confidence=None,
            )
        )

    cur.execute(NET_WORTH, (classes, liquidity, values_base, values_secondary))
    figures = cur.fetchone() or (None,) * 8
    # Python decides which total may be shown and does no arithmetic: a missing rate nulls its
    # reporting currency whole rather than letting SUM skip the account it could not translate.
    base_whole = all(value is not None for value in values_base)
    secondary_whole = all(value is not None for value in values_secondary)

    def shown(value: Decimal | None, whole: bool) -> str | None:
        return _out(value) if whole else None

    worth = NetWorth(
        asOf=as_of,
        baseCurrency=base,
        secondaryCurrency=secondary,
        totalBase=shown(figures[0], base_whole),
        totalSecondary=shown(figures[1], secondary_whole),
        assetsBase=shown(figures[2], base_whole),
        assetsSecondary=shown(figures[3], secondary_whole),
        liabilitiesBase=shown(figures[4], base_whole),
        liabilitiesSecondary=shown(figures[5], secondary_whole),
        liquidBase=shown(figures[6], base_whole),
        liquidSecondary=shown(figures[7], secondary_whole),
        rates=rates,
        missing=missing,
        investmentCoverage=sorted(
            coverage,
            key=lambda item: (
                str(item.accountId),
                "" if item.holdingId is None else str(item.holdingId),
            ),
        ),
    )
    return worth, position_composition(facts, base, secondary)


@router.get("/dashboard")
def read_dashboard(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> DashboardResponse:
    # Imported locally: `resources.py` imports this module's own VALUATION_PERIOD_FOR_ACCOUNT at
    # module load, so a top-level import of `resources.household_currencies` here would cycle.
    from ..resources import household_currencies

    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        base, secondary = household_currencies(cur, owner.household_id)
        net_worth, composition = _net_worth(cur, owner.household_id, base, secondary, as_of)
        cur.execute(FLOWS, {"as_of": as_of, "household": owner.household_id})
        flow_rows = cur.fetchall()
        trend = [
            MonthFlows(
                month=row[0],
                incomeBase=str(row[1]),
                incomeSecondary=str(row[2]),
                spendingBase=str(row[3]),
                spendingSecondary=str(row[4]),
                netBase=str(row[5]),
                netSecondary=str(row[6]),
                savingsRatePctBase=_out(row[7]),
                savingsRatePctSecondary=_out(row[8]),
            )
            for row in flow_rows
        ]
        has_recorded_flows = any(row[9] > 0 for row in flow_rows)
        coverage_months = months_from_anchor(as_of)
        evidence = flow_evidence(
            trend, coverage_months, coverage_rows(cur, owner.household_id, as_of), as_of
        )
        cur.execute(CARDS_DUE, (as_of, owner.household_id, as_of))
        cards = [
            CardDue(
                accountId=row[0],
                accountName=row[1],
                currency=row[2],
                periodEnd=row[3],
                statementBalance=str(row[4]),
                minimumDue=_out(row[5]),
                paymentDueDate=row[6],
                daysRemaining=int(row[7]),
                isPaidInFull=row[8],
                utilisationPct=_out(row[9]),
            )
            for row in cur.fetchall()
        ]
        cur.execute(YEAR_TO_DATE, {"as_of": as_of, "household": owner.household_id})
        ytd = cur.fetchone()
        assert ytd is not None
        year_to_date = YearToDate(
            yearStart=date(as_of.year, 1, 1),
            asOf=as_of,
            incomeBase=str(ytd[0]),
            incomeSecondary=str(ytd[1]),
            spendingBase=str(ytd[2]),
            spendingSecondary=str(ytd[3]),
            netBase=str(ytd[4]),
            netSecondary=str(ytd[5]),
            savingsRatePctBase=_out(ytd[6]),
            savingsRatePctSecondary=_out(ytd[7]),
            investedBase=str(ytd[8]),
            investedSecondary=str(ytd[9]),
        )
        upcoming_commitments = _upcoming_commitments(
            cur, owner.household_id, base, secondary, as_of
        )
    return DashboardResponse(
        netWorth=net_worth,
        thisMonth=trend[-1],
        trend=trend,
        hasRecordedFlows=has_recorded_flows,
        flowEvidence=evidence,
        cardsDue=cards,
        yearToDate=year_to_date,
        upcomingCommitments=upcoming_commitments,
        positionComposition=composition,
    )
