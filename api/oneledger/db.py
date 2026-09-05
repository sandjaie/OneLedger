# api/oneledger/db.py
from __future__ import annotations

import os
from urllib.parse import quote

import psycopg


def _user() -> str:
    return os.environ.get("POSTGRES_USER", "oneledger")


def _password() -> str:
    try:
        return os.environ["POSTGRES_PASSWORD"]
    except KeyError:
        raise SystemExit("POSTGRES_PASSWORD is not set. Copy .env.example to .env.") from None


def dsn() -> str:
    """The connection as a URI, for anything that needs a string.

    Percent-encoded, matching scripts/dsn.sh. A password is invited to be real, and `@`, `/`,
    `:` and `#` are all legal in one and all structural in a URI — interpolating them raw
    changes how libpq parses the string and authentication fails for no visible reason.
    """
    return (
        f"postgresql://{quote(_user(), safe='')}:{quote(_password(), safe='')}"
        f"@{os.environ.get('POSTGRES_HOST', '127.0.0.1')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'oneledger')}"
    )


def connect() -> psycopg.Connection:
    """Connect with keyword parameters rather than a URI.

    Nothing is parsed, so no character in a password can change the meaning of the
    connection. dsn() exists for the places that genuinely need a string.
    """
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "oneledger"),
        user=_user(),
        password=_password(),
    )
