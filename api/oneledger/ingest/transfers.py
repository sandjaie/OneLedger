"""Scoring a candidate transfer pair, as a function of values.

`transfers-and-reconciliation.html` §3 owns the algorithm; this implements it. The weights,
the 5% cross-currency tolerance and the per-kind date windows are that page's figures, named
here as constants so a test can pin them to it rather than to whatever this file happens to do.

**Nothing here links anything.** Scoring proposes; only `confirm_transfer()` links, and only
when the owner has asked for that pair by name (ADR-0007, refined by ADR-0017). A wrong silent
link corrupts spending totals in a way that stays invisible for months, which is why even a
score of 1.0 is a suggestion.

`ingest/` may not import from `routes/`, so this module issues no query: the market rate, the
pair history and the rule hint arrive as arguments. It reads no database and writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

# §3's weighted sum. They total 1.0, and a test says so — a weight edited in isolation
# otherwise moves every score without moving any threshold.
AMOUNT_WEIGHT = 0.35
DATE_WEIGHT = 0.25
DESCRIPTION_WEIGHT = 0.20
PAIR_HISTORY_WEIGHT = 0.15
RULE_HINT_WEIGHT = 0.05

# Same currency: a transfer that arrives short by a deducted fee is the ordinary case, so the
# curve falls away over 5% rather than at the first paisa.
_SAME_CURRENCY_EXACT = Decimal("0.001")
_SAME_CURRENCY_SLOPE = 20.0
# Cross-currency: an exchange house takes 0.5–3%, so a strict match would miss every genuine
# remittance. Below 5% is a plausible transfer; above it is more likely a coincidence.
_SPREAD_TOLERANCE = 0.05

# A pair the owner has confirmed this many times is established. §3's "confirmed eleven times"
# is an illustration of the effect, not the threshold, and reading it as one would leave the
# signal contributing almost nothing for the first ten.
_PAIR_HISTORY_SATURATES_AT = 3

# Banking verbs and rails, and no institution, currency or country among them (ADR-0012).
# These live in a string set and are data: nothing here becomes an identifier.
_DIRECTION_KEYWORDS = frozenset(
    {
        "REMIT",
        "REMITTANCE",
        "TT",
        "WIRE",
        "TRANSFER",
        "IMPS",
        "NEFT",
        "RTGS",
        "INWARD",
        "OUTWARD",
        "SENT",
        "RECEIVED",
    }
)

# A reference shorter than this appears in half a ledger by chance, and matching on it would
# make coincidence look like evidence.
_REFERENCE_MINIMUM = 4

_TOKEN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class Leg:
    """One side of a candidate pair. The rule fields are what 3a2's cascade proposed for this
    row; they are read, never written."""

    transaction_id: UUID
    account_id: UUID
    institution_id: UUID | None
    booked_date: date
    amount: Decimal
    currency: str
    description_clean: str
    counterparty_raw: str | None
    reference_raw: str | None
    is_transfer_candidate: bool
    counterparty_account_id: UUID | None


@dataclass(frozen=True)
class Evidence:
    """What the caller knows and this module cannot ask for.

    `market_rate` converts the debit's currency to the credit's **for the debit's booked
    date**, and is None when no rate is held for that date — which scores nothing rather than
    a guessed something (Invariant 7).
    """

    kind: str
    market_rate: Decimal | None
    confirmed_pairs: int


def date_window(kind: str, *, same_institution: bool) -> int:
    """§3's table, in days. Same bank is usually instant; cross-bank has to clear."""
    if kind == "remittance":
        return 7
    if kind == "card_payment":
        return 5
    if kind == "internal_same_currency":
        return 1 if same_institution else 3
    return 3


def propose_kind(debit: Leg, credit: Leg, *, destination_is_card: bool) -> str:
    """The kind a pair looks like, so the route does not answer this a second way.

    A card is decided first: the posting lag the window exists for does not stop applying
    because the card is denominated somewhere else.
    """
    if destination_is_card:
        return "card_payment"
    if debit.currency != credit.currency:
        return "remittance"
    return "internal_same_currency"


def band(score: float) -> str:
    """§3's presentation thresholds. There is deliberately no band meaning 'linked': a value
    this function could return is a value a caller could act on without asking (ADR-0007)."""
    if score >= 0.90:
        return "high"
    if score >= 0.70:
        return "probable"
    if score >= 0.50:
        return "weak"
    return "hidden"


def _tokens(text: str) -> frozenset[str]:
    return frozenset(match.group().upper() for match in _TOKEN.finditer(text))


def _amount_score(debit: Leg, credit: Leg, market_rate: Decimal | None) -> float:
    source, destination = abs(debit.amount), abs(credit.amount)
    if debit.currency == credit.currency:
        difference = abs(source - destination) / source
        if difference < _SAME_CURRENCY_EXACT:
            return 1.0
        return max(0.0, 1.0 - float(difference) * _SAME_CURRENCY_SLOPE)
    if market_rate is None or market_rate <= 0:
        return 0.0
    expected = source * market_rate
    difference = abs(expected - destination) / expected
    return max(0.0, 1.0 - float(difference) / _SPREAD_TOLERANCE)


def _date_score(debit: Leg, credit: Leg, window: int) -> float:
    # Money arrives after it leaves. A credit dated before its debit is a different movement,
    # not a close one.
    days = (credit.booked_date - debit.booked_date).days
    if days < 0 or days >= window:
        return 0.0
    return 1.0 - days / window


def _names_the_other(one: Leg, other: Leg) -> bool:
    """Whether one leg's reference or counterparty appears in the other's narration — the one
    signal that identifies a specific movement rather than a kind of movement."""
    narration = other.description_clean.upper()
    for value in (one.reference_raw, one.counterparty_raw):
        if value is None:
            continue
        candidate = value.strip().upper()
        if len(candidate) >= _REFERENCE_MINIMUM and candidate in narration:
            return True
    return False


def _description_score(debit: Leg, credit: Leg) -> float:
    debit_tokens, credit_tokens = (
        _tokens(debit.description_clean),
        _tokens(credit.description_clean),
    )
    union = debit_tokens | credit_tokens
    overlap = len(debit_tokens & credit_tokens) / len(union) if union else 0.0
    keyed = bool(debit_tokens & _DIRECTION_KEYWORDS) and bool(credit_tokens & _DIRECTION_KEYWORDS)
    named = _names_the_other(debit, credit) or _names_the_other(credit, debit)
    return min(1.0, overlap + 0.35 * keyed + 0.40 * named)


def _pair_history_score(confirmed_pairs: int) -> float:
    return min(max(confirmed_pairs, 0), _PAIR_HISTORY_SATURATES_AT) / _PAIR_HISTORY_SATURATES_AT


def _rule_hint_score(debit: Leg, credit: Leg) -> float:
    # The flag alone names no pair: it says this row looks like a transfer, not which peer.
    if not debit.is_transfer_candidate or debit.counterparty_account_id is None:
        return 0.0
    return 1.0 if debit.counterparty_account_id == credit.account_id else 0.0


def score_match(debit: Leg, credit: Leg, evidence: Evidence) -> float:
    """How much this pair looks like one movement, in [0, 1]. A float, never money."""
    if debit.account_id == credit.account_id:
        return 0.0
    # `>=` and `<=`, so a zero leg fails both halves rather than passing one.
    if debit.amount >= 0 or credit.amount <= 0:
        return 0.0

    window = date_window(
        evidence.kind,
        same_institution=debit.institution_id is not None
        and debit.institution_id == credit.institution_id,
    )
    score = (
        AMOUNT_WEIGHT * _amount_score(debit, credit, evidence.market_rate)
        + DATE_WEIGHT * _date_score(debit, credit, window)
        + DESCRIPTION_WEIGHT * _description_score(debit, credit)
        + PAIR_HISTORY_WEIGHT * _pair_history_score(evidence.confirmed_pairs)
        + RULE_HINT_WEIGHT * _rule_hint_score(debit, credit)
    )
    # §3's own text. It cannot bind while every component is bounded to [0, 1] and the
    # weights sum to one — kept so a component that later escapes its bound caps rather than
    # outranks a perfect match, and said here so a reviewer does not hunt for its test.
    return min(score, 1.0)
