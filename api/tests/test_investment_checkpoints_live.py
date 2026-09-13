"""Live summary checkpoint reconciliation without manufactured financial facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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


def _append_purchase(fixture: StagingFixture) -> None:
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, '2026-03-01', 'purchase', 2.50000000, 10.000000, 25.0000,
                 0.0000, 0.0000, -25.0000, %s, '2026-03-01', 1.0000000000,
                 1.0000000000, -25.0000, -25.0000, 'statement',
                 'detailed_fund_statement_v1', 'SYNTHETIC-DETAIL-EVENT', NULL, NULL,
                 NULL, NULL)""",
            (fixture.account_id, fixture.security_id, fixture.currency),
        )
        assert cur.fetchone() is not None
    fixture.connection.commit()


def _prepared_checkpoint(
    fixture: StagingFixture,
    *,
    units: str = "2.50000000",
    cost: str = "25.0000",
    value: str = "30.0000",
    fingerprint: str = f"{901:064x}",
) -> staging.PreparedImport:
    checkpoint = PositionCheckpointDraft(
        1,
        date(2026, 6, 30),
        Decimal(units),
        Decimal(cost),
        Decimal(value),
        fixture.currency,
        len(units.partition(".")[2]),
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
    with fixture.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_transaction),
                 (SELECT count(*) FROM investment_unit_effect),
                 (SELECT count(*) FROM acquisition_lot),
                 (SELECT count(*) FROM price_snapshot)"""
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
