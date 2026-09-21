"""Slice 1.3 tasks 3b3a and 3b3b: transfers through the app.

Candidates are computed over committed transactions, not stored, and confirmed on the
transactions screen (ADR-0017, superseding ADR-0007). **The reads write nothing.** Scoring
proposes; only `POST /transfers`, naming the pair, links it — through `confirm_transfer()`, the
one path that moves both legs' `transfer_id` and `txn_type` together (V20).
A wrong silent link removes a genuine expense from spending and
stays invisible for months, which is why even a score of 1.0 is returned as a suggestion.

The scorer is `ingest/transfers.py`, and both candidate routes call it: §6 of
`transfers-and-reconciliation.html` asks for manual linking "not a second implementation". It
issues no query, so this module assembles what it cannot ask for — the market rate, the pair
history and the rule hint — and hands them over as values.

Phase 4.4 adds the two ends `FX_COST` never had (spec `docs/superpowers/specs/
2026-09-21-phase-4-4-fx-remittance-analysis.md`): `PATCH /transfers/{id}` records the provider
and the explicit fee no statement itemises, and `GET /transfers/analysis` groups the stored
figures by corridor, year and provider through the pure `remittance_analysis` module. The fee is
translated once, when it is recorded, at the transfer's own date. The analysis translates nothing
and derives no money figure from a rate: it reads `fx_rate` only for `_APPROXIMATE`, the
carried-forward flag `GET /transfers` already reports, so a rate corrected later can change
whether a cost is labelled approximate but never the cost itself (ADR-0002).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..ingest.engine import row_captures
from ..ingest.transfers import Evidence, Leg, band, propose_kind, score_match, stated_remittance
from ..money import MONEY, _fixed, _out, translate
from ..remittance_analysis import ProviderGroup, TransferFact, analyse
from .transactions import (
    COLUMNS,
    FIELDS,
    FROM,
    TransactionRow,
    public_transaction_row,
    transaction_row,
)

router = APIRouter(tags=["transfers"])

# The scan is the ledger joined against itself. A year bounds it at a size a request can
# answer while holding a connection; the default is a quarter.
MAX_WINDOW_DAYS = 366
DEFAULT_WINDOW_DAYS = 90
# The widest window §3 gives any kind. A credit later than this scores nothing on date, so the
# candidate scan does not join it at all.
LOOKAHEAD_DAYS = 7
# Manual linking looks further: the owner is choosing, and a remittance that took ten days is
# still theirs to link even though the scorer's date signal has given up on it.
MANUAL_LOOKAHEAD_DAYS = 31
PEERS_LIMIT = 20
SHOWN = frozenset({"high", "probable", "weak"})

# The leg's ledger row, then what the scorer reads that the ledger row does not carry.
LEG_COLUMNS = (
    COLUMNS
    + """, a.institution_id, t.reference_raw, t.counterparty_raw,
          COALESCE(r.set_is_transfer_candidate, false), r.set_counterparty_account_id,
          a.account_type::text, newest.metadata_patterns"""
)
# The newest version of the layout that imported the row — the one an import would use now
# (`routes/ingest.py`'s `_profiles`) — for the keys it reads a narration with
# (`multi-currency-and-fx.html` §5). Newest, not the row's own version: a row imported before
# its layout learned the keys is read too. A manual row has no layout and reads nothing.
LEG_FROM = (
    FROM
    + " LEFT JOIN classification_rule r ON r.id = t.classification_rule_id"
    + " LEFT JOIN statement_layout sl ON sl.id = t.statement_layout_id"
    + " AND sl.household_id = t.household_id"
    + " LEFT JOIN LATERAL (SELECT l.metadata_patterns FROM statement_layout l"
    + " WHERE l.household_id = sl.household_id AND l.name = sl.name"
    + " ORDER BY l.version DESC LIMIT 1) newest ON true"
)

# A row that can be a leg at all: not already linked, not excluded, not a split parent — a
# parent is a container whose children already carry its movement.
_LINKABLE = """{t}.transfer_id IS NULL AND NOT {t}.is_excluded
               AND NOT EXISTS (SELECT 1 FROM transaction k
                                WHERE k.parent_transaction_id = {t}.id)"""
_NOT_REJECTED = """NOT EXISTS (SELECT 1 FROM transfer x
                                WHERE x.status = 'rejected'
                                  AND x.source_transaction_id = d.id
                                  AND x.destination_transaction_id = c.id)"""


class Candidate(BaseModel):
    debit: TransactionRow
    credit: TransactionRow
    # A float and never money: a confidence in [0, 1], not an amount of anything.
    score: float
    band: str
    proposedKind: str


class CandidatesResponse(BaseModel):
    candidates: list[Candidate]
    total: int


class PeersResponse(BaseModel):
    peers: list[Candidate]


class TransferRow(BaseModel):
    id: UUID
    sourceAccountId: UUID
    destinationAccountId: UUID
    sourceTransactionId: UUID | None
    destinationTransactionId: UUID | None
    sourceAmount: str
    sourceCurrency: str
    destinationAmount: str
    destinationCurrency: str
    transferDate: date
    settlementDate: date | None
    impliedRate: str | None
    marketRate: str | None
    fxSpreadPct: str | None
    fxSpreadCostSource: str | None
    explicitFeeAmount: str | None
    explicitFeeCurrency: str | None
    totalCostSource: str | None
    transferKind: str
    provider: str | None
    matchConfidence: str | None
    matchMethod: str
    status: str
    notes: str | None
    # Invariant 7: a cost derived from a carried-forward rate is not a cost anyone observed.
    # Derived on read from the rate row rather than stored, so it cannot drift from it.
    costIsApproximate: bool


class TransfersResponse(BaseModel):
    transfers: list[TransferRow]
    total: int


# Invariant 7: a cost derived from a carried-forward rate is not a cost anyone observed.
# Derived on read from the rate rows rather than stored, so it cannot drift from them, and
# written once here so `GET /transfers` and `GET /transfers/analysis` cannot answer it two ways.
# The guard is the cost itself, not `market_rate`: Phase 4.4 lets a fee alone carry a cost on a
# same-currency remittance, which has no market rate to be carried forward. The fee's own
# currency joins the source and destination because `PATCH` translates a third-currency fee at
# this same date, so that cost rests on that rate too. Unaliased `transfer` on purpose: the
# correlated subquery names its columns that way.
_APPROXIMATE = """total_cost_source IS NOT NULL AND EXISTS (
        SELECT 1 FROM fx_rate f JOIN household h ON h.id = transfer.household_id
         WHERE f.rate_date = transfer.transfer_date
           AND f.quote_currency = h.base_currency
           AND f.base_currency IN (transfer.source_currency, transfer.destination_currency,
                                   transfer.explicit_fee_currency)
           AND f.is_carried_forward)"""

TRANSFER_COLUMNS = f"""id, source_account_id, destination_account_id, source_transaction_id,
    destination_transaction_id, source_amount, source_currency, destination_amount,
    destination_currency, transfer_date, settlement_date, implied_rate, market_rate,
    fx_spread_pct, fx_spread_cost_source, explicit_fee_amount, explicit_fee_currency,
    total_cost_source, transfer_kind::text, provider, match_confidence, match_method,
    status::text, notes,
    {_APPROXIMATE}"""
TRANSFER_FIELDS = list(TransferRow.model_fields)
_DECIMAL_FIELDS = {
    "sourceAmount",
    "destinationAmount",
    "impliedRate",
    "marketRate",
    "fxSpreadPct",
    "fxSpreadCostSource",
    "explicitFeeAmount",
    "totalCostSource",
    "matchConfidence",
}


def pair_rate(
    to_base: Mapping[str, Decimal], base: str, source: str, destination: str
) -> Decimal | None:
    """1 `source` = <returned> `destination`, from rates quoted as 1 <currency> = <rate> base.

    None when either leg has no rate — never parity, which would read two currencies' figures
    as one currency's (Invariant 7). Fixed at the precision rates are stored at, as
    `money._frozen` fixes its own crosses.
    """
    if source == destination:
        return Decimal(1)
    source_rate = Decimal(1) if source == base else to_base.get(source)
    destination_rate = Decimal(1) if destination == base else to_base.get(destination)
    if source_rate is None or destination_rate is None:
        return None
    return _fixed(source_rate / destination_rate)


class _Loaded:
    """One transaction as the wire shows it and as the scorer reads it."""

    def __init__(self, values: tuple[Any, ...]) -> None:
        width = len(FIELDS)
        self.row = transaction_row(values[:width])
        (
            institution,
            reference,
            counterparty,
            flagged,
            peer_account,
            account_type,
            patterns,
        ) = values[width:]
        stated = row_captures(patterns, self.row.descriptionRaw)
        self.is_card = account_type == "credit_card"
        self.leg = Leg(
            transaction_id=self.row.id,
            account_id=self.row.accountId,
            institution_id=institution,
            booked_date=self.row.bookedDate,
            amount=Decimal(self.row.amount),
            currency=self.row.currency,
            description_clean=self.row.descriptionClean or self.row.descriptionRaw,
            counterparty_raw=counterparty,
            reference_raw=reference,
            is_transfer_candidate=bool(flagged),
            counterparty_account_id=peer_account,
            remittance=stated_remittance(
                stated.get("remit_amount"), stated.get("remit_currency"), stated.get("remit_rate")
            ),
        )


def _load(cur: psycopg.Cursor[Any], household_id: UUID, ids: set[UUID]) -> dict[UUID, _Loaded]:
    if not ids:
        return {}
    cur.execute(
        f"SELECT {LEG_COLUMNS} {LEG_FROM} WHERE t.household_id = %s AND t.id = ANY(%s)",
        (household_id, list(ids)),
    )
    loaded = [_Loaded(values) for values in cur.fetchall()]
    return {item.row.id: item for item in loaded}


class _Scorer:
    """Everything the scorer needs that only the database knows, fetched once per request."""

    def __init__(self, cur: psycopg.Cursor[Any], household_id: UUID, legs: list[_Loaded]) -> None:
        cur.execute("SELECT base_currency FROM household WHERE id = %s", (household_id,))
        found = cur.fetchone()
        self.base: str = found[0] if found else ""
        cur.execute(
            """SELECT source_account_id, destination_account_id, count(*) FROM transfer
                WHERE household_id = %s AND status = 'confirmed' GROUP BY 1, 2""",
            (household_id,),
        )
        self.history = {(row[0], row[1]): int(row[2]) for row in cur.fetchall()}
        days = sorted({item.leg.booked_date for item in legs})
        self.rates: dict[date, dict[str, Decimal]] = {}
        if days:
            cur.execute(
                """SELECT rate_date, base_currency, rate FROM fx_rate
                    WHERE quote_currency = %s AND rate_date = ANY(%s)""",
                (self.base, days),
            )
            for on, currency, rate in cur.fetchall():
                self.rates.setdefault(on, {})[currency] = Decimal(rate)

    def market_rate(self, debit: _Loaded, credit: _Loaded) -> Decimal | None:
        """The rate for the debit's booked date: the day the money left is the day it was
        converted, whatever day it arrived. None for one currency — there is nothing to
        convert — and None where no rate is held for the day."""
        if debit.leg.currency == credit.leg.currency:
            return None
        return pair_rate(
            self.rates.get(debit.leg.booked_date, {}),
            self.base,
            debit.leg.currency,
            credit.leg.currency,
        )

    def score(self, debit: _Loaded, credit: _Loaded) -> Candidate:
        kind = propose_kind(debit.leg, credit.leg, destination_is_card=credit.is_card)
        evidence = Evidence(
            kind=kind,
            market_rate=self.market_rate(debit, credit),
            confirmed_pairs=self.history.get((debit.leg.account_id, credit.leg.account_id), 0),
        )
        scored = score_match(debit.leg, credit.leg, evidence)
        return Candidate(
            debit=debit.row,
            credit=credit.row,
            score=round(scored, 4),
            band=band(scored),
            proposedKind=kind,
        )


def _ranked(found: list[Candidate]) -> list[Candidate]:
    return sorted(
        found,
        key=lambda c: (
            -c.score,
            -c.debit.bookedDate.toordinal(),
            str(c.debit.id),
            str(c.credit.id),
        ),
    )


def _public_candidate(candidate: Candidate) -> Candidate:
    return candidate.model_copy(
        update={
            "debit": public_transaction_row(candidate.debit),
            "credit": public_transaction_row(candidate.credit),
        }
    )


def _window(
    cur: psycopg.Cursor[Any], household_id: UUID, date_from: date | None, date_to: date | None
) -> tuple[date, date] | None:
    if date_to is None:
        # The ledger's latest row, not today: an owner whose statements lag three months is
        # not told there is nothing to confirm.
        cur.execute(
            "SELECT max(booked_date) FROM transaction WHERE household_id = %s", (household_id,)
        )
        latest = cur.fetchone()
        if latest is None or latest[0] is None:
            return None
        date_to = latest[0]
    assert date_to is not None
    if date_from is None:
        date_from = date_to - timedelta(days=DEFAULT_WINDOW_DAYS)
    return date_from, date_to


def _check_window(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise HTTPException(status_code=422, detail="dateFrom is after dateTo")
        if (date_to - date_from).days > MAX_WINDOW_DAYS:
            raise HTTPException(
                status_code=422,
                detail=f"the window is at most {MAX_WINDOW_DAYS} days; narrow dateFrom or dateTo",
            )


def candidates_for_window(
    cur: psycopg.Cursor[Any], household_id: UUID, date_from: date, date_to: date
) -> list[Candidate]:
    """Scored candidates over debits in `[date_from, date_to]`, ranked, filtered to the shown
    bands — the computation `GET /transfers/candidates` paginates and the monthly review's
    transfers step counts. Not a second scorer: both callers reach this one function."""
    cur.execute(
        f"""SELECT d.id, c.id
              FROM transaction d
              JOIN transaction c
                ON c.household_id = d.household_id
               AND c.account_id <> d.account_id
               AND c.booked_date BETWEEN d.booked_date AND d.booked_date + %s
             WHERE d.household_id = %s
               AND d.booked_date BETWEEN %s AND %s
               AND d.amount < 0 AND c.amount > 0
               AND {_LINKABLE.format(t="d")} AND {_LINKABLE.format(t="c")}
               AND {_NOT_REJECTED}""",
        (LOOKAHEAD_DAYS, household_id, date_from, date_to),
    )
    pair_ids = [(row[0], row[1]) for row in cur.fetchall()]
    loaded = _load(cur, household_id, {i for pair in pair_ids for i in pair})
    scorer = _Scorer(cur, household_id, list(loaded.values()))
    scored = [scorer.score(loaded[d], loaded[c]) for d, c in pair_ids]
    return _ranked([candidate for candidate in scored if candidate.band in SHOWN])


@router.get("/transfers/candidates")
def list_candidates(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    dateFrom: Annotated[date | None, Query()] = None,
    dateTo: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CandidatesResponse:
    """Scored unlinked pairs across the ledger, ranked. Writes nothing."""
    _check_window(dateFrom, dateTo)
    with connect() as conn, conn.cursor() as cur:
        window = _window(cur, owner.household_id, dateFrom, dateTo)
        if window is None:
            return CandidatesResponse(candidates=[], total=0)
        _check_window(*window)
        shown = candidates_for_window(cur, owner.household_id, *window)
    return CandidatesResponse(
        candidates=[_public_candidate(item) for item in shown[offset : offset + limit]],
        total=len(shown),
    )


@router.get("/transactions/{transaction_id}/transfer-candidates")
def list_peers(
    transaction_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> PeersResponse:
    """Ranked peers for one transaction, for manual linking, including the weak ones §3 keeps
    off the candidate list — this is where it says they are findable."""
    with connect() as conn, conn.cursor() as cur:
        subject = _load(cur, owner.household_id, {transaction_id}).get(transaction_id)
        # One answer for "no such transaction" and "another household's": telling them apart
        # would tell a caller whether an id they guessed exists.
        if subject is None:
            raise HTTPException(status_code=404, detail="transaction not found")
        if subject.row.transferId is not None or subject.leg.amount == 0:
            return PeersResponse(peers=[])
        is_debit = subject.leg.amount < 0
        peer, window = (
            ("c", "c.booked_date BETWEEN d.booked_date AND d.booked_date + %s")
            if is_debit
            else ("d", "d.booked_date BETWEEN c.booked_date - %s AND c.booked_date")
        )
        fixed = "d" if is_debit else "c"
        cur.execute(
            f"""SELECT {peer}.id
                  FROM transaction d, transaction c
                 WHERE {fixed}.id = %s
                   AND d.household_id = %s AND c.household_id = %s
                   AND c.account_id <> d.account_id
                   AND d.amount < 0 AND c.amount > 0
                   AND {window}
                   AND {_LINKABLE.format(t=peer)}
                   AND {_NOT_REJECTED}""",
            (transaction_id, owner.household_id, owner.household_id, MANUAL_LOOKAHEAD_DAYS),
        )
        # The household predicates above bound the scan; the boundary is `_load`, which
        # re-filters every peer by household before anything reaches a response. Either
        # alone keeps another household's row out — the second is why both are kept.
        peer_ids = {row[0] for row in cur.fetchall()}
        loaded = _load(cur, owner.household_id, peer_ids)
        scorer = _Scorer(cur, owner.household_id, [subject, *loaded.values()])
        scored = [
            scorer.score(subject, item) if is_debit else scorer.score(item, subject)
            for item in loaded.values()
        ]
    return PeersResponse(
        peers=[
            _public_candidate(item)
            for item in _ranked([c for c in scored if c.score > 0])[:PEERS_LIMIT]
        ]
    )


@router.get("/transfers")
def list_transfers(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    status: Annotated[str | None, Query(pattern="^(confirmed|rejected)$")] = None,
    accountId: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransfersResponse:
    """Transfers already decided, newest first. `candidate` is not a status this can be asked
    for: candidates are computed, not stored (ADR-0017)."""
    clauses = ["household_id = %s", "status IN ('confirmed', 'rejected')"]
    params: list[Any] = [owner.household_id]
    if status is not None:
        clauses.append("status = %s::transfer_status")
        params.append(status)
    if accountId is not None:
        clauses.append("(source_account_id = %s OR destination_account_id = %s)")
        params.extend([accountId, accountId])
    where = " AND ".join(clauses)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM transfer WHERE {where}", tuple(params))
        counted = cur.fetchone()
        cur.execute(
            f"""SELECT {TRANSFER_COLUMNS} FROM transfer WHERE {where}
                 ORDER BY transfer_date DESC, created_at DESC, id DESC LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        )
        rows = cur.fetchall()
    return TransfersResponse(
        transfers=[_transfer_row(values) for values in rows],
        total=int(counted[0]) if counted else 0,
    )


def _transfer_row(values: tuple[Any, ...]) -> TransferRow:
    fields = dict(zip(TRANSFER_FIELDS, values, strict=True))
    for name in _DECIMAL_FIELDS:
        fields[name] = _out(fields[name])
    return TransferRow(**fields)


def _read_transfer(cur: psycopg.Cursor[Any], transfer_id: UUID, household_id: UUID) -> TransferRow:
    cur.execute(
        f"SELECT {TRANSFER_COLUMNS} FROM transfer WHERE id = %s AND household_id = %s",
        (transfer_id, household_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="transfer not found")
    return _transfer_row(found)


class TransferCreate(BaseModel):
    sourceTransactionId: UUID
    destinationTransactionId: UUID
    # True when the owner is confirming a pair the scorer proposed, false when they picked the
    # pair themselves. It decides `match_method` and whether a confidence is recorded; the
    # score itself is recomputed here and never taken from the request.
    fromCandidate: bool = False
    notes: str | None = Field(default=None, max_length=500)


# `multi-currency-and-fx.html` §5, in NUMERIC. Each figure derives from the one stored before
# it — the spread from the stored implied and market rates, the cost from the stored spread —
# so every stored figure can be recomputed from the figures stored beside it, the rule
# `money._frozen` applies to a transaction's frozen amounts. A null market rate nulls
# everything derived from it: a blank, never a guess (Invariant 7). No fee is stated by any
# statement this ledger reads, so `explicit_fee_amount` stays null and the total is the spread.
FX_COST = """
    WITH observed AS (
        SELECT id, round(destination_amount / source_amount, 10) AS implied,
               %s::numeric AS market
          FROM transfer WHERE id = %s
    ), spread AS (
        SELECT id, implied, market, round((market - implied) / market * 100, 4) AS pct
          FROM observed
    )
    UPDATE transfer t
       SET implied_rate = spread.implied,
           market_rate = spread.market,
           fx_spread_pct = spread.pct,
           fx_spread_cost_source = round(t.source_amount * spread.pct / 100, 4),
           total_cost_source = round(t.source_amount * spread.pct / 100, 4),
           updated_at = now()
      FROM spread
     WHERE t.id = spread.id"""


@router.post("/transfers", status_code=201)
def create_transfer(
    body: TransferCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> TransferRow:
    """Confirm a proposed pair, or link one the owner picked, through `confirm_transfer()`.

    Nothing calls this but a request naming both legs: there is no code path from a score to a
    link (ADR-0007). One transaction, so a refusal anywhere writes nothing at all.
    """
    with connect() as conn, conn.cursor() as cur:
        loaded = _load(
            cur, owner.household_id, {body.sourceTransactionId, body.destinationTransactionId}
        )
        debit = loaded.get(body.sourceTransactionId)
        credit = loaded.get(body.destinationTransactionId)
        # One answer for "no such transaction" and "another household's".
        if debit is None or credit is None:
            raise HTTPException(status_code=404, detail="transaction not found")
        if debit.leg.account_id == credit.leg.account_id:
            raise HTTPException(
                status_code=422, detail="a transfer moves money between two accounts"
            )
        if debit.leg.amount >= 0 or credit.leg.amount <= 0:
            raise HTTPException(
                status_code=422,
                detail="the source must be money leaving and the destination money arriving",
            )
        # Unreachable-by-construction behind the sign check above: a debit can only be a
        # source and a credit only a destination, so a leg reused in its own role is refused
        # by V20's unique indexes with this same 409. Kept so the refusal happens before any
        # write, and says the same thing if a later route relaxes the sign rule.
        if debit.row.transferId is not None or credit.row.transferId is not None:
            raise HTTPException(
                status_code=409, detail="a transaction is already a leg of a confirmed transfer"
            )

        scorer = _Scorer(cur, owner.household_id, [debit, credit])
        proposal = scorer.score(debit, credit)
        cur.execute(
            """INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                                     source_transaction_id, destination_transaction_id,
                                     source_amount, source_currency, destination_amount,
                                     destination_currency, transfer_date, transfer_kind,
                                     match_confidence, match_method, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::transfer_kind, %s, %s, %s)
               RETURNING id""",
            (
                owner.household_id,
                debit.leg.account_id,
                credit.leg.account_id,
                debit.row.id,
                credit.row.id,
                abs(debit.leg.amount),
                debit.leg.currency,
                credit.leg.amount,
                credit.leg.currency,
                debit.leg.booked_date,
                proposal.proposedKind,
                Decimal(str(round(proposal.score, 3))) if body.fromCandidate else None,
                "auto" if body.fromCandidate else "user_created",
                body.notes,
            ),
        )
        created = cur.fetchone()
        assert created is not None
        transfer_id = created[0]

        scope_to_owner(conn, owner.user_id)
        try:
            cur.execute("SELECT confirm_transfer(%s)", (transfer_id,))
            # Also unreachable from this route today — the one shape only the trigger catches,
            # a transaction as source here and destination there, needs a leg of each sign.
            # `trg_one_transfer_per_leg` is deferred to commit. Asked for here, a leg another
            # confirmed transfer already claims is a 409 on this request rather than a failed
            # commit after the response has been built.
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except psycopg.errors.UniqueViolation:
            raise HTTPException(
                status_code=409, detail="a transaction is already a leg of a confirmed transfer"
            ) from None
        except psycopg.errors.InsufficientPrivilege:
            raise HTTPException(status_code=404, detail="transaction not found") from None

        if debit.leg.currency != credit.leg.currency:
            cur.execute(FX_COST, (scorer.market_rate(debit, credit), transfer_id))
        return _read_transfer(cur, transfer_id, owner.household_id)


class ProviderRowOut(BaseModel):
    provider: str | None
    transferCount: int
    sentSource: str
    costedSentSource: str
    feesSource: str | None
    spreadSource: str | None
    totalCostSource: str | None
    effectiveCostPct: str | None
    avgRate: str | None
    approximateCount: int


class CorridorYearOut(BaseModel):
    year: int
    totalSentSource: str
    totalCostedSentSource: str
    totalFeesSource: str | None
    totalSpreadSource: str | None
    totalCostSource: str | None
    totalEffectiveCostPct: str | None
    cheapestProvider: str | None
    potentialSavingsSource: str | None
    providers: list[ProviderRowOut]
    unattributed: ProviderRowOut | None


class CorridorOut(BaseModel):
    sourceCurrency: str
    destinationCurrency: str
    years: list[CorridorYearOut]


class RemittanceAnalysisResponse(BaseModel):
    corridors: list[CorridorOut]


_ANALYSIS_SQL = f"""
    SELECT source_currency, destination_currency, transfer_date, source_amount,
           destination_amount, implied_rate, fx_spread_cost_source, total_cost_source, provider,
           {_APPROXIMATE}
      FROM transfer
     WHERE household_id = %(household)s
       AND status = 'confirmed'
       AND transfer_kind = 'remittance'
       AND (%(year)s::int IS NULL OR EXTRACT(YEAR FROM transfer_date) = %(year)s::int)
     ORDER BY transfer_date, id
"""


def _provider_row_out(group: ProviderGroup) -> ProviderRowOut:
    return ProviderRowOut(
        provider=group.provider,
        transferCount=group.transfer_count,
        sentSource=_out(group.sent_source),
        costedSentSource=_out(group.costed_sent_source),
        feesSource=_out(group.fees_source),
        spreadSource=_out(group.spread_source),
        totalCostSource=_out(group.total_cost_source),
        effectiveCostPct=_out(group.effective_cost_pct),
        avgRate=_out(group.avg_rate),
        approximateCount=group.approximate_count,
    )


@router.get("/transfers/analysis")
def remittance_analysis(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    year: Annotated[int | None, Query(ge=1900, le=2999)] = None,
) -> RemittanceAnalysisResponse:
    """Provider cost, effective rate and the cheapest channel, by corridor and year.

    Read-only, and no money figure here is derived from a rate: every cost was frozen on its
    transfer when the owner confirmed or corrected it (ADR-0002), so a later rate backfill cannot
    change a past year's stated cost. The one `fx_rate` read is `_APPROXIMATE`, which reports
    whether that frozen cost rests on a carried-forward rate (Invariant 7) — a rate later
    observed can change that label, and nothing else.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(_ANALYSIS_SQL, {"household": owner.household_id, "year": year})
        facts = [
            TransferFact(
                source_currency=str(row[0]).strip(),
                destination_currency=str(row[1]).strip(),
                transfer_date=row[2],
                source_amount=Decimal(row[3]),
                destination_amount=Decimal(row[4]),
                implied_rate=None if row[5] is None else Decimal(row[5]),
                fx_spread_cost_source=None if row[6] is None else Decimal(row[6]),
                total_cost_source=None if row[7] is None else Decimal(row[7]),
                provider=row[8],
                cost_is_approximate=bool(row[9]),
            )
            for row in cur.fetchall()
        ]

    return RemittanceAnalysisResponse(
        corridors=[
            CorridorOut(
                sourceCurrency=corridor.source_currency,
                destinationCurrency=corridor.destination_currency,
                years=[
                    CorridorYearOut(
                        year=year_row.year,
                        totalSentSource=_out(year_row.total_sent_source),
                        totalCostedSentSource=_out(year_row.total_costed_sent_source),
                        totalFeesSource=_out(year_row.total_fees_source),
                        totalSpreadSource=_out(year_row.total_spread_source),
                        totalCostSource=_out(year_row.total_cost_source),
                        totalEffectiveCostPct=_out(year_row.total_effective_cost_pct),
                        cheapestProvider=year_row.cheapest_provider,
                        potentialSavingsSource=_out(year_row.potential_savings_source),
                        providers=[_provider_row_out(g) for g in year_row.providers],
                        unattributed=(
                            None
                            if year_row.unattributed is None
                            else _provider_row_out(year_row.unattributed)
                        ),
                    )
                    for year_row in corridor.years
                ],
            )
            for corridor in analyse(facts)
        ]
    )


class TransferCorrection(BaseModel):
    """The two fields no statement states, plus the note. An omitted key leaves the stored value
    alone; an explicit `null` clears it — told apart by `model_fields_set`, so "clear the fee"
    and "leave the fee" are different requests rather than the same empty body."""

    provider: str | None = None
    explicitFeeAmount: str | None = None
    explicitFeeCurrency: str | None = None
    notes: str | None = Field(default=None, max_length=500)


_PROVIDER_MAX = 80


def _checked_provider(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    # A whitespace-only string is a mistake, not a clear: clearing is an explicit null, so
    # silently emptying the field here would lose a provider the owner meant to correct.
    if not trimmed or len(trimmed) > _PROVIDER_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"provider must be 1 to {_PROVIDER_MAX} characters, or null to clear it",
        )
    return trimmed


def _checked_fee(amount: str | None, currency: str | None) -> tuple[Decimal, str] | None:
    """The resulting fee after the patch, refusing a half-stated or over-precise one.

    Paired nullness is the ledger's own `original_pair` rule: an amount without its currency is
    a number with no meaning.
    """
    if (amount is None) != (currency is None):
        raise HTTPException(
            status_code=422,
            detail="an explicit fee needs both explicitFeeAmount and explicitFeeCurrency",
        )
    if amount is None or currency is None:
        return None
    # The ledger's own wire-money rule, not a second one: `MONEY` bounds the amount to what
    # `NUMERIC(20,4)` holds and rejects an exponent form, `NaN` and a leading `+`, so an
    # over-large fee is refused here rather than surfacing as a numeric overflow from the UPDATE.
    if not MONEY.fullmatch(amount):
        raise HTTPException(
            status_code=422,
            detail="explicitFeeAmount must be a decimal string with at most four decimal places",
        )
    parsed = Decimal(amount)
    if parsed < 0:
        raise HTTPException(status_code=422, detail="explicitFeeAmount must not be negative")
    code = currency.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise HTTPException(
            status_code=422, detail="explicitFeeCurrency must be a three-letter code"
        )
    return parsed, code


def _fee_in_source(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    fee: tuple[Decimal, str],
    source_currency: str,
    transfer_date: date,
) -> Decimal:
    """The fee in the transfer's own currency, at the transfer's own date.

    Translated once, here, and only its effect on `total_cost_source` is stored, so the analysis
    never re-converts a frozen amount on read (ADR-0002). A date with no rate for the pair is
    refused rather than translated with a nearby one (Invariant I7).
    """
    amount, code = fee
    if code == source_currency:
        return amount

    cur.execute(
        "SELECT base_currency FROM household WHERE id = %s",
        (household_id,),
    )
    found = cur.fetchone()
    base = str(found[0]).strip() if found else ""
    cur.execute(
        """SELECT base_currency, rate FROM fx_rate
            WHERE quote_currency = %s AND rate_date = %s""",
        (base, transfer_date),
    )
    to_base = {str(row[0]).strip(): Decimal(row[1]) for row in cur.fetchall()}
    rate = pair_rate(to_base, base, code, source_currency)
    if rate is None or rate <= 0:
        raise HTTPException(
            status_code=422,
            detail="no exchange rate is held for the fee's currency on the transfer's date",
        )
    return translate(amount, rate)


_CORRECTABLE_SQL = """
    SELECT source_currency, transfer_date, fx_spread_cost_source, explicit_fee_amount,
           explicit_fee_currency
      FROM transfer
     WHERE id = %s AND household_id = %s AND status = 'confirmed'
       AND transfer_kind = 'remittance'
"""


@router.patch("/transfers/{transfer_id}")
def correct_transfer(
    transfer_id: UUID,
    body: TransferCorrection,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> TransferRow:
    """Record the provider and the explicit fee for one confirmed remittance.

    Last-write-wins, and it states no expected prior state: `total_cost_source` is recomputed
    from the stored spread plus the submitted fee, never adjusted by it, so the same correction
    sent twice stores the same figures. `implied_rate`, `market_rate`, `fx_spread_pct` and
    `fx_spread_cost_source` stay owned by confirmation-time `FX_COST` and are never written here.
    """
    sent = body.model_fields_set
    with connect() as conn, conn.cursor() as cur:
        # The same household lock every ledger writer takes, before anything is read for update.
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(_CORRECTABLE_SQL, (transfer_id, owner.household_id))
        found = cur.fetchone()
        # One answer for "no such transfer", "another household's", "not confirmed" and "not a
        # remittance": telling them apart would tell a caller whether an id they guessed exists.
        if found is None:
            raise HTTPException(status_code=404, detail="transfer not found")
        source_currency, transfer_date, spread, stored_fee, stored_fee_currency = found

        provider = _checked_provider(body.provider) if "provider" in sent else None

        assignments = ["updated_at = now()"]
        values: list[Any] = []
        if "provider" in sent:
            assignments.insert(0, "provider = %s")
            values.insert(0, provider)
        if "notes" in sent:
            assignments.insert(0, "notes = %s")
            values.insert(0, body.notes)

        # Only a submitted fee is translated, and only then is the total recomputed. A
        # provider- or notes-only correction says nothing about money, so it must not re-read a
        # rate and re-derive a cost that was frozen when the fee was recorded (ADR-0002): a rate
        # corrected since would otherwise silently move a past year's stated cost, and a rate
        # row since removed would refuse a correction about no money at all.
        if {"explicitFeeAmount", "explicitFeeCurrency"} & sent:
            # An omitted half of the pair keeps its stored value, so sending one amends the fee
            # rather than half-clearing it.
            fee_amount = body.explicitFeeAmount if "explicitFeeAmount" in sent else _out(stored_fee)
            fee_currency = (
                body.explicitFeeCurrency if "explicitFeeCurrency" in sent else stored_fee_currency
            )
            fee = _checked_fee(fee_amount, fee_currency)
            translated: Decimal | None = None
            if fee is not None:
                # A code this deployment does not hold is refused even when no translation is
                # needed, so a fee in the source currency cannot smuggle in an unseeded currency.
                cur.execute("SELECT 1 FROM currency WHERE code = %s", (fee[1],))
                if cur.fetchone() is None:
                    raise HTTPException(status_code=422, detail="unknown explicitFeeCurrency")
                translated = _fee_in_source(
                    cur, owner.household_id, fee, str(source_currency).strip(), transfer_date
                )
            # Null only when nothing is known: no spread computed and no fee recorded. Otherwise
            # the two parts sum, each absent part counting as nothing rather than blanking it.
            total_cost = (
                None
                if spread is None and translated is None
                else (Decimal(spread or 0) + (translated or Decimal(0)))
            )
            assignments.insert(0, "total_cost_source = %s")
            values.insert(0, total_cost)
            if fee is None:
                assignments.insert(0, "explicit_fee_amount = NULL, explicit_fee_currency = NULL")
            else:
                assignments.insert(0, "explicit_fee_amount = %s, explicit_fee_currency = %s")
                values.insert(0, fee[1])
                values.insert(0, fee[0])

        # One statement: every refusal above happens before it, so a rejected correction leaves
        # provider, fee and cost exactly as they were.
        cur.execute(
            f"UPDATE transfer SET {', '.join(assignments)} WHERE id = %s AND household_id = %s",
            (*values, transfer_id, owner.household_id),
        )
        return _read_transfer(cur, transfer_id, owner.household_id)


@router.delete("/transfers/{transfer_id}")
def unlink_transfer(
    transfer_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> TransferRow:
    """Release both legs through `unlink_transfer()`. The row is kept as `rejected`, which is
    what stops the pair being proposed again."""
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        try:
            cur.execute("SELECT unlink_transfer(%s)", (transfer_id,))
        except psycopg.errors.NoDataFound, psycopg.errors.InsufficientPrivilege:
            raise HTTPException(status_code=404, detail="transfer not found") from None
        return _read_transfer(cur, transfer_id, owner.household_id)
