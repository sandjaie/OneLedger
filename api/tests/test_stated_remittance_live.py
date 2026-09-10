"""A remittance whose narration states what arrives, from the statement to the candidate list.

`multi-currency-and-fx.html` §5: the source leg of an outbound remittance can carry the amount
that arrives, its currency and the rate achieved. The seeded current-account profile captures
them into `raw_row` at parse; the transfer routes read them back through the staged row each
ledger row came from, and a credit arriving as stated is proposed near-certain — where §3's
market-rate comparison alone left the same pair below the line.

Skipped when no database is reachable. Every figure is invented: 76 at 4.0789 is the 310.00
debited, and the fixture's rate of 4 would expect 77.50 to arrive.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest

from oneledger import auth
from oneledger.main import app

from .fixtures.synthetic_pdf import Placement, build_pdf
from .test_import_review_live import Staged, _all_row_ids, _connect
from .test_ingest_route_live import LAYOUT_ID, SALT
from .test_transfers_live import a_leg, an_account, candidates

SEED = Path(__file__).resolve().parents[2] / "db" / "seeds" / "account-layout.json"


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Staged]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    try:
        fixture = Staged(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        # A test that failed mid-statement leaves the transaction aborted, and a cleanup that
        # cannot run leaves rows every later live fixture refuses to start on.
        conn.rollback()
        with conn.cursor() as cur:
            # `transfer` references `transaction`, so the cascade takes it.
            cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
            cur.execute("DELETE FROM statement_layout")
            cur.execute("DELETE FROM account WHERE name = 'route test'")
            cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")
            cur.execute("DELETE FROM app_user WHERE email LIKE 'review-%@example.invalid'")
        conn.commit()
        conn.close()


def _remittance_statement(currency: str) -> bytes:
    """One debit taking 1,000.00 to 690.00, whose narration says 76 arrived at 4.0789."""
    return build_pdf(
        [
            Placement(page=0, x=20, y=760, text="Acme Bank Statement"),
            Placement(page=0, x=20, y=750, text="Account Number 1234567890"),
            Placement(page=0, x=20, y=740, text="Opening Balance 1,000.00"),
            Placement(page=0, x=40, y=700, text="Date"),
            Placement(page=0, x=140, y=700, text="Description"),
            Placement(page=0, x=420, y=700, text="Amount"),
            Placement(page=0, x=500, y=700, text="Balance"),
            Placement(page=0, x=40, y=670, text="01/03/2026"),
            Placement(page=0, x=140, y=670, text=f"OUTWARD REMITTANCE {currency}76@4.0789"),
            Placement(page=0, x=420, y=670, text="-310.00"),
            Placement(page=0, x=500, y=670, text="690.00"),
            Placement(page=0, x=20, y=600, text="Closing Balance 690.00"),
        ]
    )


def test_a_credit_arriving_as_the_narration_states_is_proposed_near_certain(
    live: Staged,
) -> None:
    # The seed's own captures, so the profile the owner loads is the one proved here.
    captures = {
        key: pattern
        for key, pattern in json.loads(SEED.read_text())["metadataPatterns"].items()
        if key.startswith("remit_")
    }
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET metadata_patterns = metadata_patterns || %s WHERE id = %s",
            (psycopg.types.json.Jsonb(captures), LAYOUT_ID),
        )
    live.conn.commit()

    batch = live.stage(statement=_remittance_statement(live.secondary))
    decided = live.patch(batch, {"rowIds": _all_row_ids(live, batch), "decision": "accepted"})
    assert decided.status_code == 200, decided.text
    committed = live.commit(batch)
    assert committed.status_code == 200, committed.text

    abroad = an_account(live, account_type="current", currency=live.secondary)
    arrived = a_leg(
        live,
        abroad,
        "76.0000",
        date(2026, 3, 2),
        "INWARD CREDIT",
        currency=live.secondary,
        to_base="4",
    )

    [proposed] = [c for c in candidates() if c["credit"]["id"] == str(arrived)]
    assert proposed["band"] == "high"
    assert proposed["proposedKind"] == "remittance"
