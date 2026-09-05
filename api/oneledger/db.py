# api/oneledger/db.py
from __future__ import annotations

import os

import psycopg


def dsn() -> str:
    """Built from parts, so the password exists in exactly one place.

    A DATABASE_URL in .env would be a second copy, and two copies of one value drift —
    scripts/dsn.sh assembles the identical string for shells that need one.
    """
    try:
        password = os.environ["POSTGRES_PASSWORD"]
    except KeyError:
        raise SystemExit("POSTGRES_PASSWORD is not set. Copy .env.example to .env.") from None
    return (
        f"postgresql://{os.environ.get('POSTGRES_USER', 'oneledger')}:{password}"
        f"@{os.environ.get('POSTGRES_HOST', '127.0.0.1')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'oneledger')}"
    )


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn())
