"""The layout mapper end to end (Phase 1.8 Task 1, spec acceptance criterion 1): a synthetic
statement with no saved layout is mapped in the app, reconciles in preview, is saved and imports
end to end with no code change — propose, preview, save, parse, confirm account, stage, commit.
A second statement in that layout is then identified without mapping.

Reuses `Fixture` from `test_ingest_route_live.py`: it already seeds a household, a person, an
owner session and an `Acme Bank` institution, which this suite's statements are written against.
It leaves `Fixture`'s own preseeded account and layout alone — this file's statement carries a
different account number and no layout is preseeded, so the mapping flow creates both for real.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from oneledger.main import app

from .fixtures.synthetic_pdf import Placement, build_pdf
from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, Fixture, live  # noqa: F401 - fixture import

ACCOUNT_NUMBER = "5559998881"


def _unmapped_statement(
    description_one: str = "COFFEE SHOP",
    description_two: str = "SALARY MARCH",
    closing: str = "1,490.00",
) -> bytes:
    return build_pdf(
        [
            Placement(page=0, x=20, y=760, text="Acme Bank Savings Statement"),
            Placement(page=0, x=20, y=750, text=f"Account Number {ACCOUNT_NUMBER}"),
            Placement(page=0, x=20, y=740, text="Opening Balance 1,000.00"),
            Placement(page=0, x=40, y=700, text="Date"),
            Placement(page=0, x=140, y=700, text="Description"),
            Placement(page=0, x=420, y=700, text="Amount"),
            Placement(page=0, x=40, y=670, text="13/03/2026"),
            Placement(page=0, x=140, y=670, text=description_one),
            Placement(page=0, x=420, y=670, text="-10.00"),
            Placement(page=0, x=40, y=650, text="14/03/2026"),
            Placement(page=0, x=140, y=650, text=description_two),
            Placement(page=0, x=420, y=650, text="500.00"),
            Placement(page=0, x=20, y=600, text=f"Closing Balance {closing}"),
        ]
    )


def test_an_unseen_layout_is_mapped_saved_and_imported_end_to_end(live: Fixture) -> None:  # noqa: F811
    client = TestClient(app)
    pdf = _unmapped_statement()
    try:
        _run_mapping_chain(client, live, pdf)
    finally:
        # `Fixture`'s own teardown deletes only its preseeded 'route test' account by name; the
        # account this mapping creates needs the same explicit identifier-then-account order or
        # the teardown's institution delete hits a foreign key it did not expect.
        with live.conn.cursor() as cur:
            # The same tables `_clean` truncates, done early: the account this mapping created
            # and committed rows against must be gone before `live`'s own teardown deletes the
            # institution those rows (transitively) still reference.
            cur.execute(
                """TRUNCATE investment_evidence, investment_review_item,
                                 investment_position_checkpoint, investment_event_draft,
                                 investment_import_group, investment_import,
                                 staged_transaction, import_batch, document, transaction CASCADE"""
            )
            clear_synthetic_account_valuation_periods(cur)
            cur.execute(
                """DELETE FROM account_identifier ai USING account a
                     WHERE ai.account_id = a.id AND a.name = 'Mapped test account'"""
            )
            cur.execute("DELETE FROM account WHERE name = 'Mapped test account'")
        live.conn.commit()


def _run_mapping_chain(client: TestClient, live: Fixture, pdf: bytes) -> None:  # noqa: F811
    # Propose: no saved layout exists yet, so the engine infers a generic draft.
    proposed = client.post(
        "/layouts/propose", files={"file": ("statement.pdf", pdf)}, headers=ORIGIN
    )
    assert proposed.status_code == 200, proposed.text
    draft = proposed.json()["draft"]

    # The owner maps the rest: account type, institution, balances and the account number.
    draft["name"] = "Acme Bank mapped savings"
    draft["accountType"] = "savings"
    draft["institutionId"] = str(live.institution_id)
    draft["openingBalancePattern"] = r"Opening\s*Balance\s*([0-9,.]+)"
    draft["closingBalancePattern"] = r"Closing\s*Balance\s*([0-9,.]+)"
    draft["metadataPatterns"] = {
        "institution_name": r"(Acme\s*Bank)",
        "account_number": r"Account\s*Number\s*([0-9]+)",
    }

    # Preview: the draft reconciles against the same file before anything is saved.
    preview = client.post(
        "/layouts/propose",
        files={"file": ("statement.pdf", pdf)},
        data={"draft": json.dumps(draft)},
        headers=ORIGIN,
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["reconciliation"]["status"] == "matched"
    assert preview_body["failures"] == []
    assert preview_body["identifies"] is True
    assert preview_body["accountNumberCaptures"] == 1
    assert preview_body["rowsExtracted"] == 2
    assert preview_body["draft"] == draft

    # Save: the mapping becomes a real layout row.
    saved = client.post("/layouts", json=draft, headers=ORIGIN)
    assert saved.status_code == 201, saved.text
    layout_id = saved.json()["id"]

    # Parse: the same file, no layoutId — the saved layout identifies it on its own, and with no
    # matching account yet the identity resolution proposes creating one.
    parsed = client.post("/parse", files={"file": ("statement.pdf", pdf)}, headers=ORIGIN)
    assert parsed.status_code == 200, parsed.text
    parsed_body = parsed.json()
    assert parsed_body["layoutId"] == layout_id
    assert parsed_body["accountId"] is None
    assert parsed_body["identityResolution"]["accountAction"] == "create"

    # Confirm account: the statement's own identity creates the account, reusing the institution
    # the draft already resolved.
    account_body = {
        "name": "Mapped test account",
        "institutionId": str(live.institution_id),
        "accountType": "savings",
        "currency": live.base,
        "country": "ZZ",
        "openingBalance": "1000",
        "openingBalanceDate": "2026-01-01",
    }
    confirmed = client.post(
        "/parse/account",
        files={"file": ("statement.pdf", pdf)},
        data={"layoutId": layout_id, "account": json.dumps(account_body)},
        headers=ORIGIN,
    )
    assert confirmed.status_code == 201, confirmed.text
    account_id = confirmed.json()["id"]

    # Stage: the same file, now against the confirmed account.
    staged = client.post(
        "/parse",
        files={"file": ("statement.pdf", pdf)},
        data={"accountId": account_id, "layoutId": layout_id, "stage": "true"},
        headers=ORIGIN,
    )
    assert staged.status_code == 200, staged.text
    batch_id = staged.json()["batchId"]

    # Review and commit: the review path this mapping now feeds like any other statement.
    review = client.get(f"/import/{batch_id}", headers=ORIGIN)
    assert review.status_code == 200, review.text
    row_ids = [row["id"] for group in review.json()["groups"] for row in group["rows"]]
    assert len(row_ids) == 2

    decided = client.patch(
        f"/import/{batch_id}/rows",
        json={"rowIds": row_ids, "decision": "accepted"},
        headers=ORIGIN,
    )
    assert decided.status_code == 200, decided.text

    committed = client.post(f"/import/{batch_id}/commit", headers=ORIGIN)
    assert committed.status_code == 200, committed.text
    assert committed.json()["rowsImported"] == 2

    # A second statement in the same layout, with no mapping: identified from the saved layout
    # alone, and its account matched from the statement's own identity.
    second_pdf = _unmapped_statement(description_one="GROCERY STORE", description_two="BONUS")
    second = client.post(
        "/parse", files={"file": ("statement-two.pdf", second_pdf)}, headers=ORIGIN
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["layoutId"] == layout_id
    assert second_body["identityResolution"]["status"] == "matched"
    assert second_body["accountId"] == account_id
