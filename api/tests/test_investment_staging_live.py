"""Live atomic and idempotent investment-staging contracts."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from oneledger import auth
from oneledger.identifiers import complete_identifier, masked_identifier
from oneledger.investments import staging
from oneledger.investments.adapters import (
    EvidenceDraft,
    InvestmentEventDraft,
    PositionCheckpointDraft,
    SafeReason,
    SourceEventIdentity,
    SourceTarget,
    StatementExtraction,
    StatementGroup,
)
from oneledger.investments.staging import PreparedImport, TargetResolution, prepare_import

from .live_database import (
    clear_synthetic_account_valuation_periods,
    connect_to_test_database,
)


@dataclass(frozen=True)
class StagingFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    account_id: UUID
    second_account_id: UUID
    cash_account_id: UUID
    other_account_id: UUID
    security_id: UUID
    second_security_id: UUID
    other_currency_security_id: UUID
    other_security_id: UUID
    document_ids: tuple[UUID, UUID, UUID]
    complete_target: SourceTarget
    masked_target: SourceTarget
    currency: str
    secondary_currency: str


@pytest.fixture
def live_staging() -> Iterator[StagingFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "stage-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-stage-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        row = cur.fetchone()
        assert row is not None
        currency, secondary = (str(value).strip() for value in row)
        for household, name in (
            (household_id, "staging household"),
            (other_household_id, "other staging household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, currency, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "staging owner"),
            (other_person_id, other_household_id, "other staging owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, "stage-owner@example.invalid"),
            (other_user_id, other_household_id, other_person_id, "other@example.invalid"),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )
        accounts: list[UUID] = []
        for household, person, name, kind, native_currency in (
            (
                household_id,
                person_id,
                "Synthetic primary investment",
                "mutual_fund_folio",
                currency,
            ),
            (household_id, person_id, "Synthetic second investment", "brokerage", currency),
            (household_id, person_id, "Synthetic cash", "savings", currency),
            (other_household_id, other_person_id, "Other investment", "brokerage", currency),
        ):
            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
                   VALUES (%s, %s, %s, %s, %s, 'XZ', 0, '2026-01-01') RETURNING id""",
                (household, person, name, kind, native_currency),
            )
            found = cur.fetchone()
            assert found is not None
            accounts.append(UUID(str(found[0])))
        securities: list[UUID] = []
        for household, name, native_currency in (
            (household_id, "Synthetic primary security", currency),
            (household_id, "Synthetic second security", currency),
            (household_id, "Synthetic alternate security", secondary),
            (other_household_id, "Other synthetic security", currency),
        ):
            cur.execute(
                """INSERT INTO security (
                       household_id, security_type, name, currency, country, asset_class)
                   VALUES (%s, 'mutual_fund', %s, %s, 'XZ', 'equity') RETURNING id""",
                (household, name, native_currency),
            )
            found = cur.fetchone()
            assert found is not None
            securities.append(UUID(str(found[0])))
        normalized = f"SYN{household_id.hex[:12].upper()}"
        cur.execute(
            """INSERT INTO account_identifier (
                   household_id, account_id, kind, masked_value, visible_suffix,
                   full_value, normalized_value, provenance)
               VALUES (%s, %s, 'folio', %s, %s, %s, %s, 'owner_entered')""",
            (
                household_id,
                accounts[0],
                f"****{normalized[-4:]}",
                normalized[-4:],
                normalized,
                normalized,
            ),
        )
        documents: list[UUID] = []
        for number in range(3):
            cur.execute(
                """INSERT INTO document (
                       household_id, document_type, original_filename, storage_bucket,
                       storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by)
                   VALUES (%s, 'investment_statement', 'masked-investment.pdf', 'documents',
                           %s, 'application/pdf', 100, %s, 'parsed', %s) RETURNING id""",
                (
                    household_id,
                    f"synthetic/staging-{number}",
                    f"{number + 1:064x}",
                    user_id,
                ),
            )
            found = cur.fetchone()
            assert found is not None
            documents.append(UUID(str(found[0])))
    connection.commit()
    try:
        yield StagingFixture(
            connection,
            owner,
            other_owner,
            accounts[0],
            accounts[1],
            accounts[2],
            accounts[3],
            securities[0],
            securities[1],
            securities[2],
            securities[3],
            tuple(documents),  # type: ignore[arg-type]
            SourceTarget(complete_identifier("folio", normalized), "PRIVATE SECURITY EVIDENCE"),
            SourceTarget(
                masked_identifier("folio", f"****{normalized[-4:]}"),
                "PRIVATE SECURITY EVIDENCE",
            ),
            currency,
            secondary,
        )
    finally:
        connection.rollback()
        with connection.cursor() as cur:
            cur.execute(
                """TRUNCATE investment_evidence, investment_review_item,
                            investment_position_checkpoint, investment_event_draft,
                            investment_import_group, investment_import,
                            investment_transaction, holding CASCADE"""
            )
            cur.execute("DELETE FROM document WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM account_identifier WHERE household_id = %s", (household_id,))
            cur.execute(
                "DELETE FROM security WHERE household_id IN (%s, %s)",
                (household_id, other_household_id),
            )
            clear_synthetic_account_valuation_periods(cur)
            cur.execute(
                "DELETE FROM account WHERE household_id IN (%s, %s)",
                (household_id, other_household_id),
            )
            cur.execute("DELETE FROM app_user WHERE id IN (%s, %s)", (user_id, other_user_id))
            cur.execute("DELETE FROM person WHERE id IN (%s, %s)", (person_id, other_person_id))
            cur.execute(
                "DELETE FROM household WHERE id IN (%s, %s)",
                (household_id, other_household_id),
            )
        connection.commit()
        connection.close()


def _evidence(
    order: int,
    *,
    source_id: str | None = None,
    correlation: SourceEventIdentity | None = None,
    fingerprint: str | None = None,
) -> EvidenceDraft:
    return EvidenceDraft(
        1,
        order,
        None if source_id is None else SourceEventIdentity(source_id),
        "typed_event_v1",
        fingerprint or f"{order + 100:064x}",
        correlation,
    )


def _draft(
    order: int,
    currency: str,
    *,
    source_id: str | None = None,
    correlation: SourceEventIdentity | None = None,
    units: str = "2.00000000",
    fingerprint: str | None = None,
) -> InvestmentEventDraft:
    return InvestmentEventDraft(
        order,
        date(2026, 3, order),
        "purchase",
        Decimal(units),
        None,
        Decimal("5.000000"),
        Decimal("10.0000"),
        Decimal("0.0000"),
        Decimal("0.0000"),
        Decimal("-10.0000"),
        currency,
        _evidence(
            order,
            source_id=source_id,
            correlation=correlation,
            fingerprint=fingerprint,
        ),
    )


def _checkpoint(order: int, currency: str) -> PositionCheckpointDraft:
    return PositionCheckpointDraft(
        order,
        date(2026, 3, 31),
        Decimal("2.00000000"),
        Decimal("10.0000"),
        Decimal("11.0000"),
        currency,
        8,
        EvidenceDraft(1, order, None, "typed_checkpoint_v1", f"{order + 200:064x}"),
    )


def _extraction(groups: tuple[StatementGroup, ...]) -> StatementExtraction:
    return StatementExtraction(True, "detailed_fund_statement_v1", "3", groups)


def _prepared(
    fixture: StagingFixture,
    groups: tuple[StatementGroup, ...],
    resolutions: tuple[TargetResolution, ...] | None = None,
) -> PreparedImport:
    if resolutions is None:
        resolutions = tuple(
            TargetResolution(group.group_number, fixture.account_id, fixture.security_id)
            for group in groups
        )
    return prepare_import(_extraction(groups), resolutions)


def test_only_complete_active_account_identity_is_reused(live_staging: StagingFixture) -> None:
    resolved = staging.resolve_existing_investment_account(
        live_staging.owner, live_staging.complete_target.account_identifier
    )
    masked = staging.resolve_existing_investment_account(
        live_staging.owner, live_staging.masked_target.account_identifier
    )

    assert resolved == live_staging.account_id
    assert masked is None
    unresolved = prepare_import(
        _extraction(
            (
                StatementGroup(
                    1,
                    live_staging.complete_target,
                    (_draft(1, live_staging.currency),),
                ),
            )
        ),
        (),
    )
    with pytest.raises(staging.StagingError, match="^investment targets are unresolved$"):
        staging.stage_prepared_import(live_staging.owner, live_staging.document_ids[0], unresolved)


def test_atomic_groups_blockers_retry_and_exact_values_are_persisted(
    live_staging: StagingFixture,
) -> None:
    correlation = SourceEventIdentity("PRIVATE-CORRELATION")
    groups = (
        StatementGroup(
            1,
            live_staging.masked_target,
            (_draft(1, live_staging.currency, correlation=correlation),),
            (_checkpoint(3, live_staging.currency),),
        ),
        StatementGroup(
            2,
            live_staging.masked_target,
            (_draft(2, live_staging.currency, correlation=correlation),),
        ),
        StatementGroup(
            3,
            live_staging.masked_target,
            (),
            reasons=(SafeReason("unrecognized_event", 2, 4),),
        ),
    )
    prepared = _prepared(
        live_staging,
        groups,
        (
            TargetResolution(1, live_staging.account_id, live_staging.security_id),
            TargetResolution(2, live_staging.second_account_id, live_staging.second_security_id),
            TargetResolution(3, live_staging.account_id, live_staging.security_id),
        ),
    )

    created = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )
    retried = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[0], prepared
    )

    assert created.created is True
    assert retried.created is False
    assert retried.import_id == created.import_id
    assert retried.group_ids == created.group_ids
    assert created.status == "awaiting_review"
    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT group_number, status::text FROM investment_import_group
                 WHERE import_id = %s ORDER BY group_number""",
            (created.import_id,),
        )
        assert cur.fetchall() == [(1, "ready"), (2, "blocked")]
        cur.execute(
            """SELECT group_id, source_order, units::text, price::text, gross_amount::text,
                      net_amount::text, currency::text, state::text
                 FROM investment_event_draft ORDER BY source_order"""
        )
        drafts = cur.fetchall()
        assert len(drafts) == 2
        assert drafts[0][0] == drafts[1][0] == created.group_ids[0]
        assert drafts[0][2:] == (
            "2.00000000",
            "5.000000",
            "10.0000",
            "-10.0000",
            live_staging.currency,
            "ready",
        )
        cur.execute(
            """SELECT reported_units::text, reported_cost_native::text,
                      reported_value_native::text, declared_unit_scale
                 FROM investment_position_checkpoint"""
        )
        assert cur.fetchone() == ("2.00000000", "10.0000", "11.0000", 8)
        cur.execute("""SELECT reason::text, group_id IS NOT NULL FROM investment_review_item""")
        assert cur.fetchall() == [("unrecognized_event", True)]
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_evidence),
                 (SELECT count(*) FROM investment_transaction),
                 (SELECT count(*) FROM holding),
                 (SELECT count(*) FROM acquisition_lot),
                 (SELECT count(*) FROM valuation_snapshot)"""
        )
        assert cur.fetchone() == (3, 0, 0, 0, 0)


def test_active_retry_must_match_the_already_staged_targets_and_facts(
    live_staging: StagingFixture,
) -> None:
    group = StatementGroup(1, live_staging.masked_target, (_draft(1, live_staging.currency),))
    first = _prepared(live_staging, (group,))
    staging.stage_prepared_import(live_staging.owner, live_staging.document_ids[0], first)
    changed = _prepared(
        live_staging,
        (group,),
        (TargetResolution(1, live_staging.second_account_id, live_staging.second_security_id),),
    )

    with pytest.raises(staging.StagingError, match="^investment import retry conflicts$"):
        staging.stage_prepared_import(live_staging.owner, live_staging.document_ids[0], changed)

    with live_staging.connection.cursor() as cur:
        cur.execute("SELECT account_id, security_id FROM investment_event_draft")
        assert cur.fetchone() == (live_staging.account_id, live_staging.security_id)


@pytest.mark.parametrize(
    "invalid_target", ["foreign_account", "cash_account", "foreign_security", "currency"]
)
def test_invalid_explicit_targets_roll_back_every_staging_row(
    live_staging: StagingFixture, invalid_target: str
) -> None:
    account_id = live_staging.account_id
    security_id = live_staging.security_id
    if invalid_target == "foreign_account":
        account_id = live_staging.other_account_id
    elif invalid_target == "cash_account":
        account_id = live_staging.cash_account_id
    elif invalid_target == "foreign_security":
        security_id = live_staging.other_security_id
    else:
        security_id = live_staging.other_currency_security_id
    group = StatementGroup(1, live_staging.masked_target, (_draft(1, live_staging.currency),))
    prepared = _prepared(
        live_staging,
        (group,),
        (TargetResolution(1, account_id, security_id),),
    )

    with pytest.raises(staging.StagingError, match="^investment target is invalid$"):
        staging.stage_prepared_import(live_staging.owner, live_staging.document_ids[0], prepared)

    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_import),
                 (SELECT count(*) FROM investment_import_group),
                 (SELECT count(*) FROM investment_event_draft),
                 (SELECT count(*) FROM investment_evidence)"""
        )
        assert cur.fetchone() == (0, 0, 0, 0)


def _prior_committed_event(
    fixture: StagingFixture,
    *,
    source_id: str | None,
    fingerprint: str,
) -> UUID:
    prior_document = fixture.document_ids[0]
    with fixture.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(fixture.owner.user_id),))
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, '2026-03-01', 'purchase', 2.00000000, 5.000000, 10.0000,
                 0.0000, 0.0000, -10.0000, %s, '2026-03-01', 1.0000000000,
                 1.0000000000, -10.0000, -10.0000, 'statement',
                 'detailed_fund_statement_v1', %s, %s, %s, NULL, NULL)""",
            (
                fixture.account_id,
                fixture.security_id,
                fixture.currency,
                source_id,
                None if source_id is not None else "typed_event_v1",
                None if source_id is not None else fingerprint,
            ),
        )
        event_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO investment_import (
                   household_id, document_id, source_contract, extraction_version, status,
                   completed_at)
               VALUES (%s, %s, 'detailed_fund_statement_v1', '2', 'completed', now())
               RETURNING id""",
            (fixture.owner.household_id, prior_document),
        )
        import_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO investment_import_group (
                   household_id, import_id, group_number, status, committed_at)
               VALUES (%s, %s, 1, 'committed', now()) RETURNING id""",
            (fixture.owner.household_id, import_id),
        )
        group_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """SELECT record_investment_evidence(
                 %s, %s, NULL, NULL, %s, 1::SMALLINT, 1,
                 'detailed_fund_statement_v1', '2', %s, 'typed_event_v1', %s)""",
            (group_id, prior_document, event_id, source_id, fingerprint),
        )
        assert cur.fetchone()[0] is not None  # type: ignore[index]
    fixture.connection.commit()
    return UUID(str(event_id))


@pytest.mark.parametrize("typed_identity", [True, False])
def test_exact_committed_overlap_attaches_new_evidence_without_a_draft(
    live_staging: StagingFixture, typed_identity: bool
) -> None:
    source_id = "SYNTHETIC-SOURCE-EVENT" if typed_identity else None
    old_fingerprint = f"{801:064x}"
    event_id = _prior_committed_event(
        live_staging, source_id=source_id, fingerprint=old_fingerprint
    )
    incoming_fingerprint = f"{802:064x}" if typed_identity else old_fingerprint
    group = StatementGroup(
        1,
        live_staging.masked_target,
        (
            _draft(
                1,
                live_staging.currency,
                source_id=source_id,
                fingerprint=incoming_fingerprint,
            ),
        ),
    )
    prepared = _prepared(live_staging, (group,))

    result = staging.stage_prepared_import(
        live_staging.owner, live_staging.document_ids[1], prepared
    )

    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_event_draft
                 WHERE group_id = %s""",
            (result.group_ids[0],),
        )
        assert cur.fetchone() == (0,)
        cur.execute(
            """SELECT investment_transaction_id FROM investment_evidence
                 WHERE document_id = %s""",
            (live_staging.document_ids[1],),
        )
        assert cur.fetchone() == (event_id,)
        cur.execute("SELECT count(*) FROM investment_transaction")
        assert cur.fetchone() == (1,)


def test_conflicting_overlap_blocks_only_its_group_and_attaches_nothing(
    live_staging: StagingFixture,
) -> None:
    source_id = "SYNTHETIC-CONFLICTING-EVENT"
    _prior_committed_event(live_staging, source_id=source_id, fingerprint=f"{901:064x}")
    conflict = StatementGroup(
        1,
        live_staging.masked_target,
        (_draft(1, live_staging.currency, source_id=source_id, units="3.00000000"),),
    )
    unrelated = StatementGroup(
        2,
        live_staging.masked_target,
        (_draft(2, live_staging.currency, source_id="SYNTHETIC-UNRELATED"),),
    )

    result = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[1],
        _prepared(live_staging, (conflict, unrelated)),
    )

    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT group_number, status::text FROM investment_import_group
                 WHERE import_id = %s ORDER BY group_number""",
            (result.import_id,),
        )
        assert cur.fetchall() == [(1, "blocked"), (2, "ready")]
        cur.execute(
            """SELECT reason::text, group_id FROM investment_review_item
                 WHERE import_id = %s""",
            (result.import_id,),
        )
        assert cur.fetchone() == ("duplicate_conflict", result.group_ids[0])
        cur.execute(
            """SELECT count(*) FROM investment_evidence WHERE document_id = %s""",
            (live_staging.document_ids[1],),
        )
        assert cur.fetchone() == (1,)
        cur.execute(
            """SELECT source_order FROM investment_event_draft
                 WHERE group_id IN (%s, %s)""",
            result.group_ids,
        )
        assert cur.fetchall() == [(2,)]


def test_cancellation_is_idempotent_terminal_and_preserves_only_provenance(
    live_staging: StagingFixture,
) -> None:
    group = StatementGroup(1, live_staging.masked_target, (_draft(1, live_staging.currency),))
    staged = staging.stage_prepared_import(
        live_staging.owner,
        live_staging.document_ids[0],
        _prepared(live_staging, (group,)),
    )

    cancelled = staging.cancel_staged_import(live_staging.owner, staged.import_id)
    retried = staging.cancel_staged_import(live_staging.owner, staged.import_id)

    assert cancelled.status == retried.status == "cancelled"
    assert cancelled.import_id == retried.import_id == staged.import_id
    with pytest.raises(staging.StagingError, match="^investment import is terminal$"):
        staging.stage_prepared_import(
            live_staging.owner,
            live_staging.document_ids[0],
            _prepared(live_staging, (group,)),
        )
    with live_staging.connection.cursor() as cur:
        cur.execute("SELECT status::text FROM investment_import WHERE id = %s", (staged.import_id,))
        assert cur.fetchone() == ("cancelled",)
        cur.execute(
            """SELECT status::text FROM investment_import_group WHERE import_id = %s""",
            (staged.import_id,),
        )
        assert cur.fetchone() == ("cancelled",)
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_event_draft),
                 (SELECT count(*) FROM investment_evidence),
                 (SELECT count(*) FROM investment_transaction),
                 (SELECT count(*) FROM holding),
                 (SELECT count(*) FROM acquisition_lot)"""
        )
        assert cur.fetchone() == (1, 1, 0, 0, 0)


def test_late_database_failure_rolls_back_parent_group_and_first_draft(
    live_staging: StagingFixture,
) -> None:
    first = _draft(1, live_staging.currency)
    duplicate_order = replace(_draft(2, live_staging.currency), source_order=1)
    group = StatementGroup(1, live_staging.masked_target, (first, duplicate_order))

    with pytest.raises(staging.StagingError, match="^investment import could not be staged$"):
        staging.stage_prepared_import(
            live_staging.owner,
            live_staging.document_ids[0],
            _prepared(live_staging, (group,)),
        )

    with live_staging.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_import),
                 (SELECT count(*) FROM investment_import_group),
                 (SELECT count(*) FROM investment_event_draft),
                 (SELECT count(*) FROM investment_evidence)"""
        )
        assert cur.fetchone() == (0, 0, 0, 0)
