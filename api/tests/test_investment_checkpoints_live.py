"""Live summary checkpoint reconciliation without manufactured financial facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from oneledger.investments import staging
from oneledger.investments.adapters import (
    EvidenceDraft,
    PositionCheckpointDraft,
    StatementExtraction,
    StatementGroup,
)
from oneledger.investments.staging import TargetResolution, prepare_import

from .test_investment_staging_live import StagingFixture
from .test_investment_staging_live import live_staging as live_staging


def _append_purchase(
    fixture: StagingFixture,
    *,
    units: str = "2.50000000",
    gross: str = "25.0000",
    price: str = "10.000000",
    event_date: str = "2026-03-01",
    source_id: str = "SYNTHETIC-DETAIL-EVENT",
) -> None:
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, %s, 'purchase', %s, %s, %s,
                 0.0000, 0.0000, %s, %s, %s, 1.0000000000,
                 1.0000000000, %s, %s, 'statement',
                 'detailed_fund_statement_v1', %s, NULL, NULL,
                 NULL, NULL)""",
            (
                fixture.account_id,
                fixture.security_id,
                event_date,
                Decimal(units),
                Decimal(price),
                Decimal(gross),
                -Decimal(gross),
                fixture.currency,
                event_date,
                -Decimal(gross),
                -Decimal(gross),
                source_id,
            ),
        )
        assert cur.fetchone() is not None
    fixture.connection.commit()


def _append_disposal(
    fixture: StagingFixture,
    *,
    event_date: str,
    allocated_units: tuple[str, ...] | None,
    disposed_units: str = "4.00000000",
    gross: str = "40.0000",
    price: str = "10.000000",
) -> None:
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT id FROM acquisition_lot
                 WHERE household_id = %s ORDER BY acquisition_date, id""",
            (fixture.owner.household_id,),
        )
        lots = cur.fetchall()
        assert lots
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, %s, 'sale', %s, %s, %s,
                 0.0000, 0.0000, %s, %s, %s, 1.0000000000,
                 1.0000000000, %s, %s, 'statement',
                 'detailed_fund_statement_v1', 'SYNTHETIC-DETAIL-DISPOSAL', NULL, NULL,
                 NULL, NULL)""",
            (
                fixture.account_id,
                fixture.security_id,
                event_date,
                -Decimal(disposed_units),
                Decimal(price),
                Decimal(gross),
                Decimal(gross),
                fixture.currency,
                event_date,
                Decimal(gross),
                Decimal(gross),
            ),
        )
        disposal = cur.fetchone()
        assert disposal is not None
        if allocated_units is not None:
            assert len(lots) >= len(allocated_units)
            cur.execute(
                "SELECT allocate_investment_lots(%s, %s::UUID[], %s::NUMERIC[])",
                (
                    disposal[0],
                    [lot[0] for lot in lots[: len(allocated_units)]],
                    [Decimal(units) for units in allocated_units],
                ),
            )
    fixture.connection.commit()


def _append_split_without_successor(
    fixture: StagingFixture, *, event_date: str = "2026-04-01", units: str = "10.00000000"
) -> None:
    """Insert a lineage-breaking split directly. Task 15.2 creates no successor lot for one."""
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT holding_id FROM investment_transaction
                 WHERE household_id = %s AND account_id = %s AND security_id = %s
                 LIMIT 1""",
            (fixture.owner.household_id, fixture.account_id, fixture.security_id),
        )
        holding_row = cur.fetchone()
        assert holding_row is not None
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'on', true)")
        cur.execute(
            """INSERT INTO investment_transaction (
                 household_id, account_id, holding_id, security_id, event_date, event_kind,
                 units, unit_ratio, net_amount, currency, source_kind, source_contract,
                 source_event_id
               ) VALUES (
                 %s, %s, %s, %s, %s, 'split', %s, 2, 0, %s, 'statement',
                 'detailed_fund_statement_v1', 'SYNTHETIC-INCOMPLETE-SPLIT')""",
            (
                fixture.owner.household_id,
                fixture.account_id,
                holding_row[0],
                fixture.security_id,
                event_date,
                Decimal(units),
                fixture.currency,
            ),
        )
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'off', true)")
    fixture.connection.commit()


def _prepared_checkpoint(
    fixture: StagingFixture,
    *,
    units: str | None = "2.50000000",
    cost: str | None = "25.0000",
    value: str = "30.0000",
    fingerprint: str = f"{901:064x}",
    as_of_date: date = date(2026, 6, 30),
) -> staging.PreparedImport:
    checkpoint = PositionCheckpointDraft(
        1,
        as_of_date,
        None if units is None else Decimal(units),
        None if cost is None else Decimal(cost),
        Decimal(value),
        fixture.currency,
        None if units is None else len(units.partition(".")[2]),
        EvidenceDraft(1, 6, None, "typed_summary_checkpoint_v1", fingerprint),
    )
    extraction = StatementExtraction(
        True,
        "summary_fund_statement_v1",
        "1",
        (StatementGroup(1, fixture.masked_target, (), (checkpoint,)),),
    )
    return prepare_import(
        extraction,
        (TargetResolution(1, fixture.account_id, fixture.security_id),),
    )


def _financial_counts(fixture: StagingFixture) -> tuple[int, int, int, int]:
    # price_snapshot is scoped to this fixture's own security: the table itself is not
    # household-scoped, and another live suite's shared-security prices persist in the same
    # per-session test database when several live test files run together.
    with fixture.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_transaction WHERE household_id = %(household)s),
                 (SELECT count(*) FROM investment_unit_effect WHERE household_id = %(household)s),
                 (SELECT count(*) FROM acquisition_lot WHERE household_id = %(household)s),
                 (SELECT count(*) FROM price_snapshot WHERE security_id = %(security)s)""",
            {"household": fixture.owner.household_id, "security": fixture.security_id},
        )
        row = cur.fetchone()
    assert row is not None
    return tuple(int(value) for value in row)  # type: ignore[return-value]


def test_detailed_history_and_matching_summary_confirm_the_holding_only(
    live_staging: StagingFixture,
) -> None:
    _append_purchase(live_staging)
    before = _financial_counts(live_staging)
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging, units="2.5"),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 1
    assert result.mismatched == 0
    assert result.pending == 0
    assert _financial_counts(live_staging) == before
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT checkpoint.reconciliation_status::text, checkpoint.holding_id,
                      checkpoint.reported_value_native::text,
                      count(review.id)
                 FROM investment_position_checkpoint checkpoint
                 LEFT JOIN investment_review_item review
                   ON review.checkpoint_id = checkpoint.id
                WHERE checkpoint.group_id = %s
                GROUP BY checkpoint.id""",
            (staged.group_ids[0],),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "matched"
        assert row[1] is not None
        assert row[2] == "30.0000"
        assert row[3] == 0


def test_mismatched_summary_creates_fixed_reviews_and_no_event_or_price(
    live_staging: StagingFixture,
) -> None:
    _append_purchase(live_staging)
    before = _financial_counts(live_staging)
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging, units="2.40000000", cost="24.0000"),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 0
    assert result.mismatched == 1
    assert result.pending == 0
    assert _financial_counts(live_staging) == before
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT checkpoint.reconciliation_status::text,
                      array_agg(review.reason::text ORDER BY review.reason::text)
                 FROM investment_position_checkpoint checkpoint
                 JOIN investment_review_item review ON review.checkpoint_id = checkpoint.id
                WHERE checkpoint.group_id = %s
                GROUP BY checkpoint.id""",
            (staged.group_ids[0],),
        )
        assert cur.fetchone() == ("mismatched", ["cost_mismatch", "unit_mismatch"])


def test_summary_without_detailed_events_never_manufactures_history(
    live_staging: StagingFixture,
) -> None:
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.mismatched == 1
    assert _financial_counts(live_staging) == (0, 0, 0, 0)
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT reason::text FROM investment_review_item
                 WHERE checkpoint_id IS NOT NULL ORDER BY reason::text"""
        )
        assert cur.fetchall() == [("cost_mismatch",), ("unit_mismatch",)]


def test_exact_retry_and_overlapping_summary_evidence_are_idempotent(
    live_staging: StagingFixture,
) -> None:
    _append_purchase(live_staging)
    prepared = _prepared_checkpoint(live_staging)
    first = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )
    retried = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )
    staging.reconcile_staged_checkpoints(live_staging.owner, first.import_id)
    staging.reconcile_staged_checkpoints(live_staging.owner, first.import_id)
    overlap = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[1],
        prepared,
    )
    staging.reconcile_staged_checkpoints(live_staging.owner, overlap.import_id)

    assert retried.created is False
    assert retried.import_id == first.import_id
    assert _financial_counts(live_staging) == (1, 1, 1, 0)
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*), count(*) FILTER (WHERE reconciliation_status = 'matched')
                 FROM investment_position_checkpoint"""
        )
        assert cur.fetchone() == (2, 2)
        cur.execute("SELECT count(*) FROM investment_evidence WHERE checkpoint_id IS NOT NULL")
        assert cur.fetchone() == (2,)
        cur.execute("SELECT count(*) FROM investment_review_item")
        assert cur.fetchone() == (0,)


def test_dated_complete_disposal_allocation_reconciles_retained_basis(
    live_staging: StagingFixture,
) -> None:
    _append_purchase(live_staging, units="10.00000000", gross="100.0000")
    _append_disposal(
        live_staging,
        event_date="2026-05-01",
        allocated_units=("4.00000000",),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging, units="6.00000000", cost="60.0000"),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 1
    assert result.pending == 0
    assert result.mismatched == 0


def test_disposal_after_checkpoint_does_not_reduce_dated_units_or_basis(
    live_staging: StagingFixture,
) -> None:
    _append_purchase(live_staging, units="10.00000000", gross="100.0000")
    _append_disposal(
        live_staging,
        event_date="2026-07-01",
        allocated_units=("4.00000000",),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging, units="10.00000000", cost="100.0000"),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 1
    assert result.pending == 0


@pytest.mark.parametrize("allocated_units", [None, ("2.00000000",)])
def test_incomplete_disposal_allocation_never_claims_or_invents_remaining_basis(
    live_staging: StagingFixture,
    allocated_units: tuple[str, ...] | None,
) -> None:
    _append_purchase(live_staging, units="10.00000000", gross="100.0000")
    _append_disposal(
        live_staging,
        event_date="2026-05-01",
        allocated_units=allocated_units,
    )
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging, units="6.00000000", cost="100.0000"),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 0
    assert result.mismatched == 0
    assert result.pending == 1
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT checkpoint.reconciliation_status::text, child.status::text,
                      array_agg(review.reason::text ORDER BY review.reason::text)
                 FROM investment_position_checkpoint checkpoint
                 JOIN investment_import_group child ON child.id = checkpoint.group_id
                 JOIN investment_review_item review ON review.checkpoint_id = checkpoint.id
                WHERE checkpoint.group_id = %s
                GROUP BY checkpoint.id, child.status""",
            (staged.group_ids[0],),
        )
        assert cur.fetchone() == ("pending", "ready", ["incomplete_lot_allocation"])


def test_exact_retry_after_mismatch_preserves_derived_review_state(
    live_staging: StagingFixture,
) -> None:
    _append_purchase(live_staging)
    prepared = _prepared_checkpoint(live_staging, units="2.40000000", cost="24.0000")
    first = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )
    staging.reconcile_staged_checkpoints(live_staging.owner, first.import_id)
    with live_staging.connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM investment_position_checkpoint WHERE group_id = %s",
            (first.group_ids[0],),
        )
        checkpoint_id = cur.fetchone()[0]  # type: ignore[index]
    before = _financial_counts(live_staging)

    retried = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )

    assert retried.created is False
    assert retried.import_id == first.import_id
    assert retried.group_ids == first.group_ids
    assert retried.group_states[0].reasons == ("cost_mismatch", "unit_mismatch")
    assert _financial_counts(live_staging) == before
    with live_staging.connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM investment_position_checkpoint WHERE group_id = %s",
            (first.group_ids[0],),
        )
        assert cur.fetchall() == [(checkpoint_id,)]
        cur.execute(
            """SELECT reason::text, count(*) FROM investment_review_item
                 WHERE checkpoint_id = %s GROUP BY reason ORDER BY reason""",
            (checkpoint_id,),
        )
        assert cur.fetchall() == [("cost_mismatch", 1), ("unit_mismatch", 1)]
        cur.execute(
            "SELECT count(*) FROM investment_evidence WHERE checkpoint_id = %s", (checkpoint_id,)
        )
        assert cur.fetchone() == (1,)


def test_declared_unit_precision_uses_decimal_half_up_at_the_tie(
    live_staging: StagingFixture,
) -> None:
    _append_purchase(live_staging, units="2.50000005", gross="25.0000")
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging, units="2.5000001"),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 1
    assert result.mismatched == 0


def test_value_only_checkpoint_remains_evidence_without_event_or_price(
    live_staging: StagingFixture,
) -> None:
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(live_staging, units=None, cost=None),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.pending == 1
    assert result.matched == 0
    assert _financial_counts(live_staging) == (0, 0, 0, 0)


def test_active_split_withholds_cost_and_matches_on_units_alone(
    live_staging: StagingFixture,
) -> None:
    """Task 15.2: a corporate action withholds cost silently instead of opening a review."""
    _append_purchase(live_staging, units="10.00000000", gross="100.0000")
    # A holding-level 2-for-1 split doubles units; this fixture writes that exact effect
    # directly since no operation creates a successor lot for it under Task 15.2.
    _append_split_without_successor(live_staging, event_date="2026-04-01", units="10.00000000")
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(
            live_staging, units="20.00000000", cost="100.0000", as_of_date=date(2026, 6, 30)
        ),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 1
    assert result.mismatched == 0
    assert result.pending == 0
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT checkpoint.reconciliation_status::text, count(review.id)
                 FROM investment_position_checkpoint checkpoint
                 LEFT JOIN investment_review_item review
                   ON review.checkpoint_id = checkpoint.id
                WHERE checkpoint.group_id = %s
                GROUP BY checkpoint.id""",
            (staged.group_ids[0],),
        )
        assert cur.fetchone() == ("matched", 0)


def test_active_split_still_flags_a_genuine_unit_mismatch(
    live_staging: StagingFixture,
) -> None:
    """Withholding cost must not also silence a real unit disagreement."""
    _append_purchase(live_staging, units="10.00000000", gross="100.0000")
    _append_split_without_successor(live_staging, event_date="2026-04-01", units="10.00000000")
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(
            live_staging, units="21.00000000", cost="100.0000", as_of_date=date(2026, 6, 30)
        ),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 0
    assert result.mismatched == 1
    assert result.pending == 0


def test_retained_basis_rounds_each_lot_before_summing(
    live_staging: StagingFixture,
) -> None:
    for day, source_id in ((1, "SYNTHETIC-TINY-LOT-A"), (2, "SYNTHETIC-TINY-LOT-B")):
        _append_purchase(
            live_staging,
            units="2.00000000",
            price="0.000050",
            gross="0.0001",
            event_date=f"2026-03-{day:02d}",
            source_id=source_id,
        )
    _append_disposal(
        live_staging,
        event_date="2026-05-01",
        disposed_units="2.00000000",
        price="0.000050",
        gross="0.0001",
        allocated_units=("1.00000000", "1.00000000"),
    )
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared_checkpoint(
            live_staging,
            units="2.00000000",
            cost="0.0002",
            value="0.0002",
        ),
    )

    result = staging.reconcile_staged_checkpoints(live_staging.owner, staged.import_id)

    assert result.matched == 1
    assert result.mismatched == 0
