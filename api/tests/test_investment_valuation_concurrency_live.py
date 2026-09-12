from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from .live_database import connect_to_test_database
from .live_database import test_dsn as _test_dsn


@pytest.mark.parametrize(
    ("edit_kind", "expected_constraint"),
    [
        ("currency", "account_currency_retains_valuations"),
        ("lifetime", "account_lifetime_retains_valuations"),
        ("anchor", "account_anchor_retains_valuations"),
    ],
)
def test_snapshot_insert_serializes_with_parent_edits(
    edit_kind: str, expected_constraint: str
) -> None:
    setup = connect_to_test_database()
    household_id = uuid4()
    person_id = uuid4()
    account_id = uuid4()
    with setup.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        base, secondary = cur.fetchone()  # type: ignore[misc]
        cur.execute(
            """INSERT INTO household
                     (id, name, base_currency, secondary_currency)
               VALUES (%s, %s, %s, %s)""",
            (household_id, "valuation concurrency household", base, secondary),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, %s, true)""",
            (person_id, household_id, "valuation concurrency owner"),
        )
        cur.execute(
            """INSERT INTO account
                     (id, household_id, owner_person_id, name, account_type, currency,
                      country, opening_balance, opening_balance_date)
               VALUES (%s, %s, %s, %s, 'property', %s, 'XZ', 0, '2026-01-01')""",
            (account_id, household_id, person_id, "valuation concurrency account", base),
        )
    setup.commit()
    setup.close()

    insert_conn = psycopg.connect(_test_dsn())
    edit_conn = psycopg.connect(_test_dsn())
    started = threading.Event()
    finished = threading.Event()
    outcome: dict[str, Any] = {}
    try:
        with insert_conn.cursor() as cur:
            cur.execute(
                """INSERT INTO valuation_snapshot
                         (household_id, account_id, as_of_date, value_native, currency,
                          fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                          value_base, value_secondary, method, confidence)
                   VALUES (%s, %s, '2026-03-31', 10.0000, %s, '2026-03-31',
                           1.0000000000, 1.0000000000, 10.0000, 10.0000,
                           'owner_entered', 'medium')""",
                (household_id, account_id, base),
            )

        def edit_parent() -> None:
            try:
                with edit_conn.cursor() as cur:
                    cur.execute("SET lock_timeout = '5s'")
                    started.set()
                    if edit_kind == "currency":
                        cur.execute(
                            "UPDATE account SET currency = %s WHERE id = %s",
                            (secondary, account_id),
                        )
                    elif edit_kind == "lifetime":
                        cur.execute(
                            "UPDATE account SET opened_on = '2026-04-01' WHERE id = %s",
                            (account_id,),
                        )
                    else:
                        cur.execute(
                            """UPDATE account SET opening_balance_date = '2026-04-01'
                                WHERE id = %s""",
                            (account_id,),
                        )
                edit_conn.commit()
                outcome["edit_committed"] = True
            except psycopg.errors.CheckViolation as exc:
                edit_conn.rollback()
                outcome["constraint"] = exc.diag.constraint_name
                outcome["edit_committed"] = False
            except BaseException as exc:  # captured in the worker and asserted in the main thread
                edit_conn.rollback()
                outcome["error"] = exc
            finally:
                finished.set()

        worker = threading.Thread(target=edit_parent, daemon=True)
        worker.start()
        assert started.wait(timeout=2), "the parent-edit transaction did not start"
        finished.wait(timeout=0.5)
        insert_conn.commit()
        worker.join(timeout=6)
        assert not worker.is_alive(), "the parent edit remained blocked after snapshot commit"
        assert "error" not in outcome, outcome.get("error")
        assert outcome.get("edit_committed") is False, (
            "the account edit and snapshot insert both committed an inconsistent history"
        )
        assert outcome.get("constraint") == expected_constraint
    finally:
        insert_conn.rollback()
        edit_conn.rollback()
        insert_conn.close()
        edit_conn.close()
