"""A remittance whose narration states what arrives, from the statement to the candidate list.

`multi-currency-and-fx.html` §5: the source leg of an outbound remittance can carry the amount
that arrives, its currency and the rate achieved. The transfer matcher reads them from each
ledger row's own narration under the newest version of the layout that imported it — so a
ledger whose current-account statements were all committed before the layout learned the
captures is read as soon as a newer version carries them, which is the owner's ledger exactly.

Skipped when no database is reachable. Every figure is invented: 76 at 4.0789 is the 310.00
debited, and the fixture's rate of 4 would expect 77.50 to arrive.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

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
            cur.execute("DELETE FROM household WHERE name = 'other'")
        conn.commit()
        conn.close()


NARRATION = "OUTWARD REMITTANCE {currency}76@4.0789"


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
            Placement(page=0, x=140, y=670, text=NARRATION.format(currency=currency)),
            Placement(page=0, x=420, y=670, text="-310.00"),
            Placement(page=0, x=500, y=670, text="690.00"),
            Placement(page=0, x=20, y=600, text="Closing Balance 690.00"),
        ]
    )


# The seed posted again: a newer version of the fixture's layout, the older one kept for the rows
# it imported. The household is a parameter because every household holds this same name.
_NEXT_VERSION = """
    INSERT INTO statement_layout
      (household_id, name, version, fingerprints, columns, date_format, sign_convention,
       inverts_sign, chronological_order, row_start_rule, opening_balance_pattern,
       closing_balance_pattern, has_running_balance, metadata_patterns)
    SELECT %s, name, version + %s, fingerprints, columns, date_format, sign_convention,
           inverts_sign, chronological_order, row_start_rule, opening_balance_pattern,
           closing_balance_pattern, has_running_balance, metadata_patterns || %s
      FROM statement_layout WHERE id = %s"""


def _bands(credit: UUID) -> dict[str, str]:
    """Each proposed debit's band against this credit, by debit id."""
    return {c["debit"]["id"]: c["band"] for c in candidates() if c["credit"]["id"] == str(credit)}


def test_a_remittance_already_imported_is_near_certain_once_its_layout_reads_the_narration(
    live: Staged,
) -> None:
    batch = live.stage(statement=_remittance_statement(live.secondary))
    decided = live.patch(batch, {"rowIds": _all_row_ids(live, batch), "decision": "accepted"})
    assert decided.status_code == 200, decided.text
    committed = live.commit(batch)
    assert committed.status_code == 200, committed.text
    with live.conn.cursor() as cur:
        cur.execute("SELECT id FROM transaction WHERE import_batch_id = %s", (batch,))
        imported = str(cur.fetchone()[0])  # type: ignore[index]

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
    # A manual row saying the same thing: no layout imported it, so nothing reads its narration.
    manual = a_leg(
        live,
        live.account_id,
        "-310.0000",
        date(2026, 3, 1),
        NARRATION.format(currency=live.secondary),
    )

    # Committed under a layout that reads no narration: §3's market-rate comparison alone.
    assert "high" not in _bands(arrived).values()

    captures = Jsonb(
        {
            key: pattern
            for key, pattern in json.loads(SEED.read_text())["metadataPatterns"].items()
            if key.startswith("remit_")
        }
    )
    # Another household posting the same seed is the ordinary case, and the service connects
    # past RLS. A newer version of the same name that is theirs must not read this household's
    # narrations — neither lifting them with its captures, nor hiding them without.
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('other', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        other = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(_NEXT_VERSION, (other, 4, captures, LAYOUT_ID))
    live.conn.commit()
    assert "high" not in _bands(arrived).values()

    # The owner posts the seed again, while the other household's newer version stops reading.
    with live.conn.cursor() as cur:
        cur.execute(_NEXT_VERSION, (live.household_id, 1, captures, LAYOUT_ID))
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = metadata_patterns
                      - 'remit_amount' - 'remit_currency' - 'remit_rate'
                WHERE household_id = %s""",
            (other,),
        )
    live.conn.commit()

    bands = _bands(arrived)
    assert bands[imported] == "high"
    assert bands.get(str(manual)) != "high"
    [kind] = [c["proposedKind"] for c in candidates() if c["debit"]["id"] == imported]
    assert kind == "remittance"
