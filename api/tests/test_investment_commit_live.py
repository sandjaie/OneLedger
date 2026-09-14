"""Live atomic commit contracts: one savepoint per group, replay, and the household lock."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from oneledger.investments import commit as investment_commit
from oneledger.investments import staging
from oneledger.investments.adapters import (
    EvidenceDraft,
    InvestmentEventDraft,
    PositionCheckpointDraft,
    SourceEventIdentity,
    StatementExtraction,
    StatementGroup,
)
from oneledger.investments.commands import InvestmentCommandError
from oneledger.investments.staging import TargetResolution, prepare_import
from oneledger.routes import investment_imports

from .test_investment_staging_live import StagingFixture
from .test_investment_staging_live import live_staging as live_staging

_SOURCE_CONTRACT = "detailed_fund_statement_v1"


def _evidence(
    order: int, *, correlation: SourceEventIdentity | None = None, fingerprint: str | None = None
) -> EvidenceDraft:
    return EvidenceDraft(
        1, order, None, "typed_event_v1", fingerprint or f"{order + 100:064x}", correlation
    )


def _event(
    order: int,
    event_date: date,
    kind: str,
    *,
    units: str,
    price: str,
    gross: str,
    net: str,
    currency: str,
    correlation: SourceEventIdentity | None = None,
) -> InvestmentEventDraft:
    return InvestmentEventDraft(
        order,
        event_date,
        kind,  # type: ignore[arg-type]
        Decimal(units),
        None,
        Decimal(price),
        Decimal(gross),
        Decimal("0.0000"),
        Decimal("0.0000"),
        Decimal(net),
        currency,
        _evidence(order, correlation=correlation),
    )


def _set_fx_rate(fixture: StagingFixture, event_date: str) -> None:
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES (%s, %s, %s, 0.5000000000, 'manual')
               ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                 SET rate = EXCLUDED.rate""",
            (event_date, fixture.secondary_currency, fixture.currency),
        )
    fixture.connection.commit()


def _financial_counts(fixture: StagingFixture) -> tuple[int, int]:
    with fixture.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (fixture.owner.household_id,),
        )
        events = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM investment_review_item WHERE household_id = %s AND status='open'",
            (fixture.owner.household_id,),
        )
        reviews = cur.fetchone()
    assert events is not None and reviews is not None
    return int(events[0]), int(reviews[0])


def test_commit_appends_purchase_sale_and_switch_once_and_reconciles_checkpoint(
    live_staging: StagingFixture,
) -> None:
    _set_fx_rate(live_staging, "2026-03-01")
    _set_fx_rate(live_staging, "2026-03-02")
    correlation = SourceEventIdentity("SWITCH-LEG-PAIR")
    groups = (
        StatementGroup(
            1,
            live_staging.masked_target,
            (
                _event(
                    1,
                    date(2026, 3, 1),
                    "purchase",
                    units="10.00000000",
                    price="10.000000",
                    gross="100.0000",
                    net="-100.0000",
                    currency=live_staging.currency,
                ),
                _event(
                    2,
                    date(2026, 3, 2),
                    "sale",
                    units="-4.00000000",
                    price="10.000000",
                    gross="40.0000",
                    net="40.0000",
                    currency=live_staging.currency,
                ),
            ),
        ),
        StatementGroup(
            2,
            live_staging.masked_target,
            (
                _event(
                    3,
                    date(2026, 3, 3),
                    "switch_out",
                    units="-3.00000000",
                    price="10.000000",
                    gross="30.0000",
                    net="0.0000",
                    currency=live_staging.currency,
                    correlation=correlation,
                ),
            ),
        ),
        StatementGroup(
            3,
            live_staging.masked_target,
            (
                _event(
                    4,
                    date(2026, 3, 3),
                    "switch_in",
                    units="3.00000000",
                    price="10.000000",
                    gross="30.0000",
                    net="0.0000",
                    currency=live_staging.currency,
                    correlation=correlation,
                ),
            ),
        ),
        StatementGroup(
            4,
            live_staging.masked_target,
            (),
            (
                PositionCheckpointDraft(
                    1,
                    date(2026, 3, 31),
                    Decimal("3.00000000"),
                    Decimal("30.0000"),
                    Decimal("30.0000"),
                    live_staging.currency,
                    8,
                    EvidenceDraft(1, 5, None, "typed_checkpoint_v1", f"{900:064x}"),
                ),
            ),
        ),
    )
    prepared = prepare_import(
        StatementExtraction(True, _SOURCE_CONTRACT, "3", groups),
        (
            TargetResolution(1, live_staging.account_id, live_staging.security_id),
            TargetResolution(2, live_staging.account_id, live_staging.security_id),
            TargetResolution(3, live_staging.account_id, live_staging.second_security_id),
            TargetResolution(4, live_staging.account_id, live_staging.security_id),
        ),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )

    result = investment_commit.commit_staged_import(
        live_staging.owner, staged.import_id, staged.expected_state
    )

    assert result.status == "completed"
    assert {(g.group_number, g.status) for g in result.groups} == {
        (1, "committed"),
        (2, "committed"),
        (3, "committed"),
    }
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT holding.security_id, holding.current_units::text
                 FROM holding WHERE holding.household_id = %s
                ORDER BY holding.security_id""",
            (live_staging.owner.household_id,),
        )
        units_by_security = {UUID(str(row[0])): row[1] for row in cur.fetchall()}
        assert units_by_security[live_staging.security_id] == "3.00000000"
        assert units_by_security[live_staging.second_security_id] == "3.00000000"
        cur.execute(
            """SELECT state, committed_transaction_id IS NOT NULL
                 FROM investment_event_draft WHERE household_id = %s""",
            (live_staging.owner.household_id,),
        )
        draft_states = cur.fetchall()
        assert draft_states and all(
            state == "committed" and has_id for state, has_id in draft_states
        )
        cur.execute(
            """SELECT reconciliation_status::text FROM investment_position_checkpoint
                 WHERE household_id = %s""",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchall() == [("matched",)]
        cur.execute(
            """SELECT reason::text, is_blocking FROM investment_review_item
                 WHERE household_id = %s AND status = 'open'""",
            (live_staging.owner.household_id,),
        )
        reviews = cur.fetchall()
        assert reviews == [("incomplete_lot_allocation", False)]
    events_before, reviews_before = _financial_counts(live_staging)

    replayed = investment_commit.commit_staged_import(
        live_staging.owner, staged.import_id, staged.expected_state
    )

    assert replayed.status == result.status
    assert {(g.group_number, g.status) for g in replayed.groups} == {
        (g.group_number, g.status) for g in result.groups
    }
    assert _financial_counts(live_staging) == (events_before, reviews_before)


def test_missing_fx_rate_blocks_only_its_group(live_staging: StagingFixture) -> None:
    _set_fx_rate(live_staging, "2026-04-01")
    # 2026-04-02 deliberately has no fx_rate row.
    groups = (
        StatementGroup(
            1,
            live_staging.masked_target,
            (
                _event(
                    1,
                    date(2026, 4, 1),
                    "purchase",
                    units="1.00000000",
                    price="10.000000",
                    gross="10.0000",
                    net="-10.0000",
                    currency=live_staging.currency,
                ),
            ),
        ),
        StatementGroup(
            2,
            live_staging.masked_target,
            (
                _event(
                    2,
                    date(2026, 4, 2),
                    "purchase",
                    units="1.00000000",
                    price="10.000000",
                    gross="10.0000",
                    net="-10.0000",
                    currency=live_staging.currency,
                ),
            ),
        ),
    )
    prepared = prepare_import(
        StatementExtraction(True, _SOURCE_CONTRACT, "3", groups),
        (
            TargetResolution(1, live_staging.account_id, live_staging.security_id),
            TargetResolution(2, live_staging.second_account_id, live_staging.security_id),
        ),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[1], prepared
    )

    result = investment_commit.commit_staged_import(
        live_staging.owner, staged.import_id, staged.expected_state
    )

    assert result.status == "partially_committed"
    outcomes = {g.group_number: (g.status, g.reason) for g in result.groups}
    assert outcomes[1] == ("committed", None)
    assert outcomes[2] == ("blocked", "missing_fx_rate")
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT state FROM investment_event_draft
                 WHERE household_id = %s ORDER BY source_order""",
            (live_staging.owner.household_id,),
        )
        assert [row[0] for row in cur.fetchall()] == ["committed", "ready"]
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchone() == (1,)

    state = investment_imports._load_import_state(live_staging.owner, staged.import_id)
    statuses = {group.groupNumber: group.status for group in state.groups}
    assert statuses == {1: "committed", 2: "ready"}

    # The group was left 'ready', not 'blocked': backfilling the missing rate and retrying with
    # the same expectedState (still valid — nothing about the staged content changed) must let
    # the previously blocked group commit rather than finding itself written off.
    _set_fx_rate(live_staging, "2026-04-02")

    retried = investment_commit.commit_staged_import(
        live_staging.owner, staged.import_id, staged.expected_state
    )

    assert retried.status == "completed"
    assert {(g.group_number, g.status) for g in retried.groups} == {
        (1, "committed"),
        (2, "committed"),
    }
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT state FROM investment_event_draft
                 WHERE household_id = %s ORDER BY source_order""",
            (live_staging.owner.household_id,),
        )
        assert [row[0] for row in cur.fetchall()] == ["committed", "committed"]
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchone() == (2,)


def test_foreign_import_is_404_and_stale_expected_state_is_409(
    live_staging: StagingFixture,
) -> None:
    _set_fx_rate(live_staging, "2026-05-01")
    groups = (
        StatementGroup(
            1,
            live_staging.masked_target,
            (
                _event(
                    1,
                    date(2026, 5, 1),
                    "purchase",
                    units="1.00000000",
                    price="10.000000",
                    gross="10.0000",
                    net="-10.0000",
                    currency=live_staging.currency,
                ),
            ),
        ),
    )
    prepared = prepare_import(
        StatementExtraction(True, _SOURCE_CONTRACT, "3", groups),
        (TargetResolution(1, live_staging.account_id, live_staging.security_id),),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[2], prepared
    )

    with pytest.raises(InvestmentCommandError) as missing:
        investment_commit.commit_staged_import(live_staging.owner, uuid4(), staged.expected_state)
    with pytest.raises(InvestmentCommandError) as stale:
        investment_commit.commit_staged_import(live_staging.owner, staged.import_id, "stale-state")

    assert missing.value.status_code == 404
    assert stale.value.status_code == 409
    with live_staging.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchone() == (0,)


def test_mismatched_transfer_leg_dates_refuse_the_group(live_staging: StagingFixture) -> None:
    correlation = SourceEventIdentity("MISMATCHED-LEG-PAIR")
    groups = (
        StatementGroup(
            1,
            live_staging.masked_target,
            (
                _event(
                    1,
                    date(2026, 6, 1),
                    "switch_out",
                    units="-3.00000000",
                    price="10.000000",
                    gross="30.0000",
                    net="0.0000",
                    currency=live_staging.currency,
                    correlation=correlation,
                ),
            ),
        ),
        StatementGroup(
            2,
            live_staging.masked_target,
            (
                _event(
                    2,
                    date(2026, 6, 2),  # deliberately not the out leg's date
                    "switch_in",
                    units="3.00000000",
                    price="10.000000",
                    gross="30.0000",
                    net="0.0000",
                    currency=live_staging.currency,
                    correlation=correlation,
                ),
            ),
        ),
    )
    prepared = prepare_import(
        StatementExtraction(True, _SOURCE_CONTRACT, "3", groups),
        (
            TargetResolution(1, live_staging.account_id, live_staging.security_id),
            TargetResolution(2, live_staging.account_id, live_staging.second_security_id),
        ),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )

    result = investment_commit.commit_staged_import(
        live_staging.owner, staged.import_id, staged.expected_state
    )

    assert [(g.group_number, g.status, g.reason) for g in result.groups] == [
        (1, "blocked", "database_refusal")
    ]
    with live_staging.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchone() == (0,)
        cur.execute(
            """SELECT state, committed_transaction_id FROM investment_event_draft
                 WHERE household_id = %s""",
            (live_staging.owner.household_id,),
        )
        assert all(state == "ready" and committed is None for state, committed in cur.fetchall())


def test_a_refused_draft_rolls_back_its_committable_sibling_but_not_the_other_group(
    live_staging: StagingFixture,
) -> None:
    _set_fx_rate(live_staging, "2026-07-01")
    _set_fx_rate(live_staging, "2026-07-02")
    _set_fx_rate(live_staging, "2026-07-03")
    groups = (
        StatementGroup(
            1,
            live_staging.masked_target,
            (
                # A valid, committable purchase followed — in the same group, same savepoint —
                # by a second draft whose net amount does not reconcile with its own gross,
                # charges and withheld tax: staging never validates that arithmetic, so this is
                # refused only when the database's own CHECK constraint sees the insert. The
                # first draft's already-successful append must roll back with it.
                _event(
                    1,
                    date(2026, 7, 1),
                    "purchase",
                    units="1.00000000",
                    price="10.000000",
                    gross="10.0000",
                    net="-10.0000",
                    currency=live_staging.currency,
                ),
                _event(
                    2,
                    date(2026, 7, 2),
                    "purchase",
                    units="2.00000000",
                    price="10.000000",
                    gross="20.0000",
                    net="-15.0000",  # wrong: purchase requires net = -(gross+charges+withheld)
                    currency=live_staging.currency,
                ),
            ),
        ),
        StatementGroup(
            2,
            live_staging.masked_target,
            (
                _event(
                    3,
                    date(2026, 7, 3),
                    "purchase",
                    units="1.00000000",
                    price="10.000000",
                    gross="10.0000",
                    net="-10.0000",
                    currency=live_staging.currency,
                ),
            ),
        ),
    )
    prepared = prepare_import(
        StatementExtraction(True, _SOURCE_CONTRACT, "3", groups),
        (
            TargetResolution(1, live_staging.account_id, live_staging.security_id),
            TargetResolution(2, live_staging.second_account_id, live_staging.security_id),
        ),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )

    result = investment_commit.commit_staged_import(
        live_staging.owner, staged.import_id, staged.expected_state
    )

    outcomes = {g.group_number: (g.status, g.reason) for g in result.groups}
    assert outcomes[1] == ("blocked", "database_refusal")
    assert outcomes[2] == ("committed", None)
    with live_staging.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchone() == (1,)  # only group 2's purchase
        cur.execute(
            """SELECT state, committed_transaction_id FROM investment_event_draft draft
                 JOIN investment_import_group grp ON grp.id = draft.group_id
                WHERE draft.household_id = %s AND grp.group_number = 1
                ORDER BY draft.source_order""",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchall() == [("ready", None), ("ready", None)]
        cur.execute(
            """SELECT count(*) FROM investment_review_item
                 WHERE household_id = %s AND status = 'open'""",
            (live_staging.owner.household_id,),
        )
        assert cur.fetchone() == (0,)


def test_two_documents_staging_the_same_event_commit_it_only_once(
    live_staging: StagingFixture,
) -> None:
    _set_fx_rate(live_staging, "2026-08-01")
    shared_identity = SourceEventIdentity("CROSS-DOCUMENT-SHARED-EVENT")

    def _shared_draft(fingerprint: str) -> InvestmentEventDraft:
        return InvestmentEventDraft(
            1,
            date(2026, 8, 1),
            "purchase",
            Decimal("5.00000000"),
            None,
            Decimal("10.000000"),
            Decimal("50.0000"),
            Decimal("0.0000"),
            Decimal("0.0000"),
            Decimal("-50.0000"),
            live_staging.currency,
            EvidenceDraft(1, 1, shared_identity, "typed_event_v1", fingerprint),
        )

    def _stage(document_index: int, fingerprint: str) -> staging.StagedImportResult:
        groups = (StatementGroup(1, live_staging.masked_target, (_shared_draft(fingerprint),)),)
        prepared = prepare_import(
            StatementExtraction(True, _SOURCE_CONTRACT, "3", groups),
            (TargetResolution(1, live_staging.account_id, live_staging.security_id),),
        )
        return staging.stage_prepared_import(
            live_staging.owner, live_staging.document_ids[document_index], prepared
        )

    # Both documents are staged before either is committed, so staging's own overlap check
    # (which only ever compares against already-committed events) sees nothing yet and gives
    # each its own independent draft row — proving the dedupe that matters here is commit's own,
    # not a staging-time shortcut that happened to make a second document a no-op beforehand.
    first_staged = _stage(0, f"{801:064x}")
    second_staged = _stage(1, f"{802:064x}")

    first_result = investment_commit.commit_staged_import(
        live_staging.owner, first_staged.import_id, first_staged.expected_state
    )
    with live_staging.connection.cursor() as cur:
        cur.execute(
            "SELECT current_units::text FROM holding WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        units_after_first = cur.fetchall()

    second_result = investment_commit.commit_staged_import(
        live_staging.owner, second_staged.import_id, second_staged.expected_state
    )
    with live_staging.connection.cursor() as cur:
        cur.execute(
            "SELECT current_units::text FROM holding WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        units_after_second = cur.fetchall()
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_staging.owner.household_id,),
        )
        event_count = cur.fetchone()
        cur.execute(
            """SELECT DISTINCT committed_transaction_id FROM investment_event_draft
                 WHERE household_id = %s""",
            (live_staging.owner.household_id,),
        )
        committed_ids = cur.fetchall()

    assert first_result.status == "completed"
    assert second_result.status == "completed"
    assert event_count == (1,)
    assert units_after_first == units_after_second == [("5.00000000",)]
    assert len(committed_ids) == 1  # both drafts point at the same single committed event
