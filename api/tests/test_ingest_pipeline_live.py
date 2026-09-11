"""The staging transaction, against a real database.

Everything the pipeline decides is provable on values and lives in `test_ingest_pipeline.py`.
What it *writes* is not: a stub cursor that records parameters and discards the SQL would accept
a column that does not exist, a CHECK the row violates, an FK to a table it never joins and a
transaction that does not roll back. Those are exactly the failures staging must not have, so
they are proved here or not at all.

Two properties carry the milestone and both are asserted directly: a dry run writes nothing —
`fx_rate` included — and a blocked batch leaves the database unchanged (spec §5.5).

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID

import psycopg
import pytest

from oneledger.db import connect
from oneledger.fxrange import RateSource
from oneledger.ingest import pipeline, storage
from oneledger.ingest.storage import DuplicateDocument
from oneledger.ingest.words import Page
from tests.test_ingest_pipeline import profile, statement, transaction

from .live_database import connect_to_test_database

# A card number in the filename, which is how the real ones arrive: `document.original_filename`
# must hold the masked form and nothing else (spec §5.6).
FILENAME = "statement-4111-1111-1111-1234.pdf"
MASKED = "statement-…1234.pdf"


class NeverCalled:
    """An FX source that fails the test if the pipeline reaches for it.

    Asserting "no `fx_rate` row was written" is weaker than it looks — a fetch that returned
    nothing would satisfy it. A dry run must not so much as ask.
    """

    def __init__(self) -> None:
        self.calls = 0

    def fetch_range(
        self, start: date, end: date, codes: list[str]
    ) -> dict[date, dict[str, Decimal]]:
        self.calls += 1
        raise AssertionError("a dry run must not fetch exchange rates")


def _connect() -> psycopg.Connection:
    """See `tests/live_database.py`: the test database, and only the test database."""
    return connect_to_test_database()


def _household(cur: psycopg.Cursor[Any]) -> pipeline.HouseholdContext:
    cur.execute("SELECT id, base_currency, secondary_currency FROM household LIMIT 1")
    row = cur.fetchone()
    if row is None:
        pytest.skip("no seeded household")
    return pipeline.HouseholdContext(id=row[0], base_currency=row[1], secondary_currency=row[2])


def _account(cur: psycopg.Cursor[Any], household: pipeline.HouseholdContext) -> UUID:
    cur.execute("SELECT id FROM person WHERE household_id = %s LIMIT 1", (household.id,))
    person = cur.fetchone()
    if person is None:
        pytest.skip("no seeded person")
    # ZZ is ISO 3166-1 user-assigned: reserved for private use and not any country's code,
    # which is what ADR-0012 wants of a fixture.
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                country, opening_balance_date, current_balance)
           VALUES (%s, %s, 'pipeline test', 'savings', %s, 'ZZ', DATE '2026-01-01', 0)
           RETURNING id""",
        (household.id, person[0], household.base_currency),
    )
    return UUID(str(cur.fetchone()[0]))  # type: ignore[index]


def _layout(cur: psycopg.Cursor[Any], household: pipeline.HouseholdContext) -> UUID:
    """The saved row the batch's `statement_layout_id` points at (Invariant I6).

    The profile the pipeline runs is a value, but the foreign key is real, so the row has to
    exist and carry the same id.
    """
    saved = profile()
    cur.execute(
        """INSERT INTO statement_layout
             (id, household_id, name, version, fingerprints, columns, date_format,
              sign_convention, inverts_sign, has_running_balance)
           VALUES (%s, %s, %s, %s, %s, '[]'::jsonb, %s, %s, %s, %s) RETURNING id""",
        (
            saved.id,
            household.id,
            saved.name,
            saved.version,
            saved.fingerprints,
            saved.dateFormat,
            saved.signConvention,
            saved.invertsSign,
            saved.hasRunningBalance,
        ),
    )
    return UUID(str(cur.fetchone()[0]))  # type: ignore[index]


def _rates(cur: psycopg.Cursor[Any], household: pipeline.HouseholdContext) -> None:
    """A stored rate for every date the statement books, so `ensure_rates` has no gap to fill.

    **3, not 4.** The secondary leg is a cross — `1 / rate` — and 1/4 is 0.25, which terminates,
    so a rate of 4 would let every assertion below hold under an implementation that never fixed
    the quotient's precision at all. `1 / 3` is a 28-digit Decimal that both `NUMERIC(20,10)`
    and the `Rate` annotation refuse outright, so this fixture is what makes the staging path
    prove it stores a rate rather than merely computing one.

    The rate is invented and the pair is read off the household rather than named, per ADR-0012.
    """
    for on in (date(2026, 3, 1), date(2026, 3, 2)):
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES (%s, %s, %s, 3.0000000000, 'manual')
               ON CONFLICT DO NOTHING""",
            (on, household.secondary_currency, household.base_currency),
        )


def _clean(cur: psycopg.Cursor[Any]) -> None:
    """These tests commit, so isolation is by teardown rather than by rollback."""
    cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
    cur.execute("DELETE FROM statement_layout")
    cur.execute("DELETE FROM account WHERE name = 'pipeline test'")
    cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")


class Fixture:
    def __init__(self, conn: psycopg.Connection, root: Path) -> None:
        self.conn = conn
        self.root = root
        with conn.cursor() as cur:
            _clean(cur)
            self.household = _household(cur)
            self.account_id = _account(cur, self.household)
            _layout(cur, self.household)
            _rates(cur, self.household)
        conn.commit()
        self.account = pipeline.AccountContext(
            id=self.account_id, currency=self.household.base_currency
        )

    def parse(
        self, *, stage: bool, data: bytes = b"%PDF-1.4 one", **changes: object
    ) -> pipeline.ParseResult:
        return pipeline.parse(
            self.conn,
            pages=cast("list[Page]", changes.pop("pages", statement())),
            data=data,
            original_filename=cast("str", changes.pop("original_filename", FILENAME)),
            household=self.household,
            profiles=[profile()],
            account=self.account,
            resolve_account=lambda _extraction, _profile: None,
            stage=stage,
            encrypted=bool(changes.pop("encrypted", False)),
            documents_root=self.root,
            rate_source=cast("RateSource", changes.pop("rate_source", NeverCalled())),
        )

    def counts(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            found = {}
            for table in ("document", "import_batch", "staged_transaction"):
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
                found[table] = cur.fetchone()[0]  # type: ignore[index]
            return found


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Fixture]:
    conn = _connect()
    try:
        yield Fixture(conn, tmp_path)
    finally:
        with conn.cursor() as cur:
            _clean(cur)
        conn.commit()
        conn.close()


def test_a_dry_run_writes_nothing_at_all(live: Fixture) -> None:
    """Spec §4.3's "stage=false writes nothing", asserted against every table it could touch.

    Reference data counts: `fx_rate` is the one row a parse could plausibly justify writing
    early, and writing it would make "nothing is written until Stage batch is pressed" false in
    the one place an owner would never think to look.
    """
    source = NeverCalled()
    result = live.parse(stage=False, rate_source=source)

    assert result.staged is False
    assert result.batch_id is None and result.document_id is None
    assert live.counts() == {"document": 0, "import_batch": 0, "staged_transaction": 0}
    assert source.calls == 0
    with live.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM fx_rate WHERE source <> 'manual'")
        assert cur.fetchone()[0] == 0  # type: ignore[index]
    # The dry run still answers the question it was asked.
    assert result.analysis.reconciliation.status == "matched"
    assert len(result.analysis.rows) == 2


def test_staging_writes_the_document_the_batch_and_every_row(live: Fixture) -> None:
    result = live.parse(stage=True)

    assert result.staged is True
    assert live.counts() == {"document": 1, "import_batch": 1, "staged_transaction": 2}

    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT status, rows_extracted, rows_new, reconciliation_status,
                      declared_opening_balance, declared_closing_balance, period_start, period_end,
                      statement_layout_id, layout_version, document_id
                 FROM import_batch WHERE id = %s""",
            (result.batch_id,),
        )
        batch = cur.fetchone()
    assert batch is not None
    assert batch[0] == "awaiting_review"
    assert (batch[1], batch[2]) == (2, 2)
    assert batch[3] == "matched"
    assert (batch[4], batch[5]) == (Decimal("1000.0000"), Decimal("1490.0000"))
    assert (batch[6], batch[7]) == (date(2026, 3, 1), date(2026, 3, 2))
    assert batch[8] == result.analysis.layout_id
    assert batch[9] == result.analysis.layout_version
    assert batch[10] == result.document_id


def test_every_staged_row_carries_its_frozen_pair_and_its_provenance(live: Fixture) -> None:
    """Invariant I6: a staged row names the page and row it came from. Invariant I7: each stored
    amount derives from the rate stored beside it.

    Nothing in V13 enforces that derivation — `frozen_amounts_derive` is V10's, on `transaction`
    alone; V13 carries only `accepted_row_is_committable` and `original_pair`. The guard is
    `StagedTransaction.translations_agree_with_the_stated_rate`, and its tolerance is a whole
    ulp (0.0001), so it would accept a rate rounded one way and an amount the other. That is
    why the exact stored values are asserted here rather than left to the model.
    """
    result = live.parse(stage=True)

    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT amount, amount_base, fx_rate_to_base, source_row_number, status,
                      dedupe_hash, proposed_txn_type, source_page_number, amount_secondary,
                      fx_rate_to_secondary, fx_rate_date, fx_rate_is_carried
                 FROM staged_transaction WHERE import_batch_id = %s ORDER BY source_row_number""",
            (result.batch_id,),
        )
        rows = cur.fetchall()

    assert [row[0] for row in rows] == [Decimal("-10.0000"), Decimal("500.0000")]
    # The account is in the base currency, so the base leg is unity and its translation is the
    # amount itself. The secondary leg is the cross — 1/3 — and is the one with something to
    # prove: it must be stored fixed at 10 dp, and the amount must derive from what was stored.
    assert [row[2] for row in rows] == [Decimal(1), Decimal(1)]
    assert [row[1] for row in rows] == [Decimal("-10.0000"), Decimal("500.0000")]
    assert [row[9] for row in rows] == [Decimal("0.3333333333")] * 2, "fixed, not 0.333…"
    assert [row[8] for row in rows] == [Decimal("-3.3333"), Decimal("166.6667")]
    for amount, rate, translated in ((row[0], row[9], row[8]) for row in rows):
        assert translated == (amount * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), (
            "the stored amount derives from the stored rate, not from an unrounded intermediate"
        )
    assert [row[10] for row in rows] == [date(2026, 3, 1), date(2026, 3, 2)]
    assert [row[11] for row in rows] == [False, False], "an observed rate is not carried"
    assert [row[4] for row in rows] == ["new", "new"]
    assert [row[6] for row in rows] == ["expense", "income"]
    assert all(len(row[5]) == 64 for row in rows), "the dedupe fingerprint travels with the row"
    assert [row[3] for row in rows] == [1, 2], "the row number the line came from"
    assert [row[7] for row in rows] == [1, 1], "and its page, not a null"


def test_the_stored_filename_is_masked_before_it_reaches_the_column(live: Fixture) -> None:
    """The unmasked name exists only as a local variable for the duration of the request."""
    result = live.parse(stage=True)

    with live.conn.cursor() as cur:
        cur.execute("SELECT original_filename FROM document WHERE id = %s", (result.document_id,))
        stored = cur.fetchone()[0]  # type: ignore[index]

    assert stored == MASKED
    assert "4111" not in stored


def test_a_statement_that_does_not_reconcile_stages_nothing(live: Fixture) -> None:
    """Invariant I8, reported rather than raised (spec §7.5).

    The refusal happens before the transaction opens, so "unchanged" is not a rollback that has
    to work — there is nothing to roll back. And it comes back as a result, not an exception,
    because the diagnostics panel needs the delta and the divergent row that an HTTP error's
    `detail` string could never carry.
    """
    result = live.parse(stage=True, pages=statement(closing="1,500.00"))

    assert result.staged is False, "asked to stage, and it did not"
    assert result.batch_id is None
    assert result.analysis.reconciliation.status == "mismatched"
    assert result.analysis.reconciliation.delta == Decimal("10.0000")
    assert live.counts() == {"document": 0, "import_batch": 0, "staged_transaction": 0}


def test_a_second_staging_of_the_same_period_is_refused(live: Fixture) -> None:
    """Spec §7 item 11: the gap the verdict columns cannot express.

    The second file differs by a byte, so layer 1 lets it through, and every row of it is
    verdict `new` because dedupe compares against committed rows and the first batch has not
    committed. Both would commit and collide on V10's `idx_txn_dedupe`.
    """
    first = live.parse(stage=True, data=b"%PDF-1.4 one")

    with pytest.raises(pipeline.PipelineError, match="unreviewed import") as raised:
        live.parse(stage=True, data=b"%PDF-1.4 one but regenerated")

    assert str(first.batch_id) in str(raised.value), "the batch to review or discard is named"
    assert live.counts() == {"document": 1, "import_batch": 1, "staged_transaction": 2}


def test_the_same_file_twice_is_refused_before_anything_is_parsed(live: Fixture) -> None:
    """Layer 1, on the staging path and the dry-run path alike."""
    live.parse(stage=True)

    with pytest.raises(DuplicateDocument):
        live.parse(stage=True)
    with pytest.raises(DuplicateDocument):
        live.parse(stage=False)


def test_a_rival_re_import_of_a_cancelled_file_is_refused_inside_the_staging(
    live: Fixture,
) -> None:
    """Layer 1 runs before the parse, and the parse is long — the FX fetch sits inside it.

    A cancelled file already has its row, so the checksum index cannot stop two re-imports of
    it: the staging asks layer 1 again once the household lock is held, or the file is imported
    twice. The rival stages into a second account, so the period check — which is per account —
    does not see it. The first upload is back-dated so the refusal's date proves the reused row
    was refreshed rather than left describing the abandoned attempt.
    """
    first = live.parse(stage=True)
    with live.conn.cursor() as cur:
        cur.execute("UPDATE import_batch SET status = 'cancelled' WHERE id = %s", (first.batch_id,))
        cur.execute("UPDATE document SET uploaded_at = DATE '2026-01-01'")
        other = _account(cur, live.household)
    live.conn.commit()

    real = storage.store
    rivals: list[pipeline.ParseResult] = []
    raced = False

    def a_rival_stages_first(
        data: bytes, household_id: UUID, original_filename: str, *, root: Path | None = None
    ) -> storage.StoredDocument:
        nonlocal raced
        if not raced:
            # Set before the rival runs: its own parse calls `store` through this same patch.
            raced = True
            # The routes' own connection, as the review tests race theirs: the fixture's guard
            # refuses a database with rows in it, and this one now holds the first batch.
            with connect() as rival:
                rivals.append(
                    pipeline.parse(
                        rival,
                        pages=statement(),
                        data=b"%PDF-1.4 one",
                        original_filename=FILENAME,
                        household=live.household,
                        profiles=[profile()],
                        account=pipeline.AccountContext(
                            id=other, currency=live.household.base_currency
                        ),
                        resolve_account=lambda _extraction, _profile: None,
                        stage=True,
                        documents_root=live.root,
                        rate_source=cast("RateSource", NeverCalled()),
                    )
                )
        return real(data, household_id, original_filename, root=root)

    with (
        patch.object(storage, "store", a_rival_stages_first),
        pytest.raises(DuplicateDocument) as raised,
    ):
        live.parse(stage=True)

    assert rivals and rivals[0].staged, "the rival re-imported the cancelled file"
    assert str(rivals[0].batch_id) in str(raised.value)
    assert "2026-01-01" not in str(raised.value), "the rival's upload date, not the cancelled one"
    with live.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM import_batch WHERE status <> 'cancelled'")
        assert cur.fetchone()[0] == 1  # type: ignore[index]
        cur.execute("SELECT account_id FROM document")
        assert cur.fetchone()[0] == other, "the reused row describes the live import"  # type: ignore[index]


def test_a_row_that_cannot_be_normalized_refuses_the_whole_batch(live: Fixture) -> None:
    """Staging part of a statement produces a ledger wrong in a way no later check looks for:
    the missing row's amount is simply absent, and every balance after it is quietly short."""
    broken = statement()
    broken[0].lines.insert(
        3, transaction(130.0, "03/03/2026", "OVERFLOWS THE LEDGER", "99,999,999,999,999,999.00", "")
    )

    result = live.parse(stage=True, pages=broken)

    assert result.staged is False
    assert result.analysis.failures != [], "the rows that failed travel back for the panel"
    assert live.counts() == {"document": 0, "import_batch": 0, "staged_transaction": 0}


def test_the_document_row_records_where_the_blob_is_and_what_it_is(live: Fixture) -> None:
    """Seventeen columns were written and one was asserted, so most of them could be anything.

    `storage_path` is the sharpest of them: `storage.store` writes to
    `<root>/<household>/<sha>.pdf` and the INSERT independently rebuilds that string, so nothing
    but this assertion ties the recorded path to the file that actually exists.
    """
    result = live.parse(stage=True)

    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT document_type, parsed_status, storage_bucket, storage_path, mime_type,
                      byte_size, sha256, page_count, currency, statement_layout_id,
                      layout_version, period_start, period_end, is_password_protected
                 FROM document WHERE id = %s""",
            (result.document_id,),
        )
        row = cur.fetchone()

    assert row is not None
    assert row[0] == "bank_statement", "a savings account's statement"
    assert row[1] == "parsed"
    assert (row[2], row[4]) == ("documents", "application/pdf")
    assert row[3] == f"{live.household.id}/{row[6]}.pdf"
    assert (live.root / row[3]).exists(), "the recorded path resolves to the blob that was written"
    assert sha256((live.root / row[3]).read_bytes()).hexdigest() == row[6], "content-addressed"
    assert row[5] == len(b"%PDF-1.4 one")
    assert row[7] == 1
    assert row[8] == live.household.base_currency
    assert (row[9], row[10]) == (result.analysis.layout_id, result.analysis.layout_version)
    assert (row[11], row[12]) == (date(2026, 3, 1), date(2026, 3, 2))
    assert row[13] is False, "this upload needed no password"


def test_a_card_accounts_statement_is_recorded_as_a_card_statement(live: Fixture) -> None:
    """`document_type` comes from the account's own stored type, never from a fingerprint.

    The branch merged immediately before this one made card statements importable, so writing
    every import as `bank_statement` would mislabel them in a forward-only column that
    `idx_document_period` indexes on.
    """
    with live.conn.cursor() as cur:
        # `card_has_limit` requires one, and an invented figure keeps the fixture honest.
        cur.execute(
            """UPDATE account SET account_type = 'credit_card', credit_limit = 25000.0000
                WHERE id = %s""",
            (live.account_id,),
        )
    live.conn.commit()

    result = live.parse(stage=True)

    with live.conn.cursor() as cur:
        cur.execute("SELECT document_type FROM document WHERE id = %s", (result.document_id,))
        assert cur.fetchone()[0] == "card_statement"  # type: ignore[index]


def test_a_password_protected_upload_is_recorded_as_one(live: Fixture) -> None:
    """The blob stored is the original, so for an encrypted upload it is still encrypted, and
    this column is the only record of that — the password itself is never stored."""
    result = live.parse(stage=True, encrypted=True)

    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT is_password_protected FROM document WHERE id = %s", (result.document_id,)
        )
        assert cur.fetchone()[0] is True  # type: ignore[index]


def test_a_derived_opening_is_not_stored_as_a_declared_one(live: Fixture) -> None:
    """Invariant I7. `import_batch.declared_opening_balance` has no companion flag, and the
    review screen reads the row rather than the parse response that labelled it — so a computed
    opening must not arrive in the column that means the statement stated it."""
    bare = profile(openingBalancePattern=None)
    result = pipeline.parse(
        live.conn,
        pages=statement(),
        data=b"%PDF-1.4 derived opening",
        original_filename=FILENAME,
        household=live.household,
        profiles=[bare],
        account=live.account,
        resolve_account=lambda _extraction, _profile: None,
        stage=True,
        documents_root=live.root,
        rate_source=NeverCalled(),
    )

    assert result.staged is True
    assert result.analysis.reconciliation.opening_is_derived is True
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT declared_opening_balance FROM import_batch WHERE id = %s", (result.batch_id,)
        )
        assert cur.fetchone()[0] is None  # type: ignore[index]


def test_a_reporting_pair_that_moved_while_parsing_refuses_the_staging(live: Fixture) -> None:
    """Spec B3.5: every writer of a frozen amount takes the household row lock before reading
    the pair. A parse runs for seconds — extraction, then a rate fetch — and `PATCH /household`
    in that window sees no frozen rows (this batch's are uncommitted, so invisible under MVCC)
    and succeeds. Freezing against the pair read at request start would then commit rows that
    contradict the pair `reporting_pair_is_locked()` now reports as locked by them.
    """
    # The household moves after the context was read, which is the interleaving: the rates this
    # batch fetched are for the pair it started with. XTS is ISO 4217's reserved code for
    # testing — not any real currency, per ADR-0012. Restored here rather than in the fixture's
    # teardown, so the one test that moves the household is the one that puts it back.
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO currency (code, name, symbol, decimals)
               VALUES ('XTS', 'Test currency', 'T', 2) ON CONFLICT DO NOTHING"""
        )
        cur.execute(
            "UPDATE household SET secondary_currency = 'XTS' WHERE id = %s", (live.household.id,)
        )
    live.conn.commit()
    try:
        with pytest.raises(pipeline.PipelineError, match="reporting pair changed"):
            live.parse(stage=True)
    finally:
        with live.conn.cursor() as cur:
            cur.execute(
                "UPDATE household SET secondary_currency = %s WHERE id = %s",
                (live.household.secondary_currency, live.household.id),
            )
            cur.execute("DELETE FROM currency WHERE code = 'XTS'")
        live.conn.commit()

    assert live.counts() == {"document": 0, "import_batch": 0, "staged_transaction": 0}


def test_a_scanned_file_says_so_rather_than_sending_the_owner_to_the_layout_picker(
    live: Fixture,
) -> None:
    """A wholly scanned document has no page-one text, so every fingerprint scores 0.00.

    Identifying first would refuse it as unidentifiable and offer a picker that cannot help:
    choosing a layout re-scores the same empty string and refuses again, with nothing anywhere
    saying the file is an image.
    """
    with pytest.raises(pipeline.PipelineError, match="scanned"):
        live.parse(stage=False, pages=[Page(lines=[], has_text=False)])
