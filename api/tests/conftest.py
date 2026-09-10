"""Environment these tests need before oneledger.main is imported — and, for the live tests, a
database of this run's own.

The service refuses to start without SESSION_SECRET, deliberately — so the suite has to supply
one. It is a throwaway value: nothing here signs anything a real client will ever present.

**Each pytest run gets a throwaway database on the test server.** Every live fixture empties
`transaction`, `import_batch`, `document`, `statement_layout`, `classification_rule` and
`transfer`, and on 10 September 2026 several sessions began working in parallel against one
`oneledger_test`: two of them running the live suites at once would empty each other's tables
mid-test and pass or fail on the other's rows. So the session start creates `pytest_<pid>_<n>`,
migrates it from empty with this checkout's own `db/migrations`, seeds it as
`scripts/test-db.sh up` seeds the shared one, and points both `ONELEDGER_TEST_DSN` (which the
fixtures read) and `POSTGRES_DB` (which the routes under test read) at it; the session finish
drops it. With no `ONELEDGER_TEST_DSN` nothing changes and the live tests skip, as before.

A run killed before its finish leaves its database behind; `scripts/test-db.sh down` removes
every one of them with the volume.
"""

from __future__ import annotations

import os
import pathlib
import uuid

import psycopg
import pytest

os.environ.setdefault("SESSION_SECRET", "test-secret-not-used-outside-this-suite")
os.environ.setdefault("ALLOWED_USER_EMAIL", "owner@example.com")

ROOT = pathlib.Path(__file__).resolve().parents[2]

# What `scripts/test-db.sh up` seeds into the shared database: a household with a person, so the
# live fixtures have an owner to attach an account to rather than skipping.
_HOUSEHOLD = """
DO $$
DECLARE h UUID; p UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  IF EXISTS (SELECT FROM person WHERE is_self) THEN RETURN; END IF;
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('test household', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'self', true)
  RETURNING id INTO p;
  INSERT INTO app_user (household_id, person_id, email, role)
  VALUES (h, p, 'test-owner@example.invalid', 'owner');
END $$;
"""

_created: str | None = None
_server: str | None = None


def _migrations() -> list[pathlib.Path]:
    return sorted(
        (ROOT / "db" / "migrations").glob("V*__*.sql"),
        key=lambda path: int(path.name[1:].split("__", 1)[0]),
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    global _created, _server
    shared = os.environ.get("ONELEDGER_TEST_DSN")
    if not shared:
        return
    _server = shared.rsplit("/", 1)[0]
    name = f"pytest_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(f"{_server}/postgres", autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError:
        # The live tests skip on an unreachable server, as they always have.
        return
    _created = name
    # No parameters, so psycopg sends each file as one simple query: multiple statements and
    # `$$` bodies go through as Flyway would apply them, one transaction per file.
    with psycopg.connect(f"{_server}/{name}", autocommit=True) as conn:
        for path in [*_migrations(), *sorted((ROOT / "db" / "seeds").glob("*.sql"))]:
            conn.execute(path.read_text())
        conn.execute(_HOUSEHOLD)
    os.environ["ONELEDGER_TEST_DSN"] = f"{_server}/{name}"
    os.environ["POSTGRES_DB"] = name


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _created is None or _server is None:
        return
    with psycopg.connect(f"{_server}/postgres", autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{_created}" WITH (FORCE)')
